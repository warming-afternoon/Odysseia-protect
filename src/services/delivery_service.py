"""动态溯源文件的生成、R2 交付、缓存续期和到期清理。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.dto.resource_dto import ResourceDTO
from src.services.object_store import R2ObjectStore
from src.services.traceability_service import TraceabilityService

logger = logging.getLogger(__name__)

EXPIRY_ZSET = "trace:delivery:expirations"
OBJECT_CACHE_KEYS = "trace:delivery:cache-keys"
LEADER_LOCK = "trace:delivery:cleanup-leader"


@dataclass(frozen=True)
class DeliveryResult:
    filename: str
    url: str | None = None
    file_data: bytes | None = None
    cache_hit: bool = False


@dataclass
class _LocalEntry:
    object_key: str
    filename: str
    cache_expires_at: float
    delete_after: float


@dataclass
class _LocalLockState:
    lock: asyncio.Lock
    users: int = 0


class DeliveryService:
    """同一用户与资源在滑动窗口内只生成一次个性化副本。"""

    def __init__(
        self,
        traceability: TraceabilityService,
        object_store: R2ObjectStore | None = None,
        *,
        redis_url: str | None = None,
        download_ttl: int | None = None,
        delete_grace: int | None = None,
    ):
        self.traceability = traceability
        self.object_store = object_store or R2ObjectStore()
        self.redis_url = redis_url if redis_url is not None else os.getenv("REDIS_URL")
        self.download_ttl = download_ttl or int(
            os.getenv("TRACE_DOWNLOAD_TTL_SECONDS", "1800")
        )
        self.delete_grace = delete_grace or int(
            os.getenv("TRACE_DELETE_GRACE_SECONDS", "300")
        )
        self._redis: Redis | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._local_entries: dict[str, _LocalEntry] = {}
        self._local_locks: dict[str, _LocalLockState] = {}
        self._local_guard = asyncio.Lock()

    async def start(self) -> None:
        if self.redis_url:
            try:
                redis = Redis.from_url(self.redis_url, decode_responses=True)
                await redis.ping()
                self._redis = redis
                logger.info("动态交付已连接 Redis。")
            except (RedisError, OSError):
                logger.exception("Redis 不可用，动态交付退化为进程内缓存。")
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(), name="trace-delivery-cleanup"
            )

    async def close(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    def _cache_key(self, resource: ResourceDTO, user_id: int) -> str:
        raw = ":".join(
            (
                str(user_id),
                str(resource.id),
                str(resource.source_message_id),
                self.traceability.key_id or "unconfigured",
            )
        )
        return "trace:delivery:cache:" + hashlib.sha256(raw.encode()).hexdigest()

    @asynccontextmanager
    async def _local_lock(self, name: str) -> AsyncIterator[None]:
        async with self._local_guard:
            state = self._local_locks.setdefault(
                name, _LocalLockState(asyncio.Lock())
            )
            state.users += 1
        try:
            async with state.lock:
                yield
        finally:
            async with self._local_guard:
                state.users -= 1
                if state.users == 0:
                    self._local_locks.pop(name, None)

    @staticmethod
    def _object_lock_name(object_key: str) -> str:
        digest = hashlib.sha256(object_key.encode()).hexdigest()
        return f"trace:delivery:object-lock:{digest}"

    @asynccontextmanager
    async def _lock(self, name: str, *, timeout: int = 90) -> AsyncIterator[None]:
        if self._redis is None:
            async with self._local_lock(name):
                yield
            return

        lock = self._redis.lock(name, timeout=timeout, blocking_timeout=timeout)
        try:
            acquired = await lock.acquire()
        except RedisError:
            logger.exception("获取 Redis 锁失败，退化为进程内锁。")
            async with self._local_lock(name):
                yield
            return
        if not acquired:
            raise TimeoutError("等待动态交付缓存锁超时。")
        try:
            yield
        finally:
            try:
                await lock.release()
            except RedisError:
                logger.warning("释放 Redis 锁失败：%s", name, exc_info=True)

    async def _read_entry(self, cache_key: str) -> _LocalEntry | None:
        now = time.time()
        if self._redis is not None:
            try:
                raw = await self._redis.get(cache_key)
                if raw:
                    value = json.loads(raw)
                    return _LocalEntry(
                        object_key=value["object_key"],
                        filename=value["filename"],
                        cache_expires_at=now + self.download_ttl,
                        delete_after=float(value["delete_after"]),
                    )
                return None
            except (RedisError, ValueError, KeyError, TypeError):
                logger.exception("读取 Redis 交付缓存失败，使用进程内缓存。")
        entry = self._local_entries.get(cache_key)
        if entry and entry.cache_expires_at > now:
            return entry
        return None

    async def _write_entry(self, cache_key: str, entry: _LocalEntry) -> None:
        self._local_entries[cache_key] = entry
        if self._redis is None:
            return
        value = json.dumps(
            {
                "object_key": entry.object_key,
                "filename": entry.filename,
                "delete_after": entry.delete_after,
            },
            separators=(",", ":"),
        )
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.set(cache_key, value, ex=self.download_ttl)
                pipe.zadd(EXPIRY_ZSET, {entry.object_key: entry.delete_after})
                pipe.hset(OBJECT_CACHE_KEYS, entry.object_key, cache_key)
                await pipe.execute()
        except RedisError:
            logger.exception("写入 Redis 交付缓存失败；生命周期规则将负责兜底。")

    async def deliver(
        self,
        resource: ResourceDTO,
        *,
        user_id: int,
        source_loader: Callable[[], Awaitable[bytes]],
    ) -> DeliveryResult:
        cache_key = self._cache_key(resource, user_id)
        async with self._lock(f"{cache_key}:create"):
            entry = await self._read_entry(cache_key)
            if entry is not None and self.object_store.available:
                async with self._lock(self._object_lock_name(entry.object_key)):
                    now = time.time()
                    entry.cache_expires_at = now + self.download_ttl
                    entry.delete_after = now + self.download_ttl + self.delete_grace
                    await self._write_entry(cache_key, entry)
                    url = await self.object_store.presign_get(
                        entry.object_key, expires_in=self.download_ttl
                    )
                return DeliveryResult(
                    filename=entry.filename,
                    url=url,
                    cache_hit=True,
                )

            source = await source_loader()
            personalized = await self.traceability.personalize(
                source,
                filename=resource.filename or "card.png",
                user_id=user_id,
                public_thread_id=resource.public_thread_id,
                resource_id=resource.id,
            )
            if not self.object_store.available:
                return DeliveryResult(
                    filename=personalized.filename,
                    file_data=personalized.data,
                )

            delivery_id = uuid.uuid4().hex
            object_key = (
                f"deliveries/{time.strftime('%Y/%m/%d')}/"
                f"{resource.id}/{delivery_id}.png"
            )
            try:
                await self.object_store.put_bytes(
                    object_key,
                    personalized.data,
                    content_type="image/png",
                    metadata={
                        "resource-id": str(resource.id),
                        "trace-key-id": self.traceability.key_id or "unknown",
                    },
                )
                now = time.time()
                entry = _LocalEntry(
                    object_key=object_key,
                    filename=personalized.filename,
                    cache_expires_at=now + self.download_ttl,
                    delete_after=now + self.download_ttl + self.delete_grace,
                )
                await self._write_entry(cache_key, entry)
                url = await self.object_store.presign_get(
                    object_key, expires_in=self.download_ttl
                )
                return DeliveryResult(filename=personalized.filename, url=url)
            except Exception:
                logger.exception("R2 动态交付失败，改用 Discord 私密附件。")
                return DeliveryResult(
                    filename=personalized.filename,
                    file_data=personalized.data,
                )

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await self.cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("动态交付清理轮次失败。")
            await asyncio.sleep(60)

    async def cleanup_once(self) -> int:
        if not self.object_store.available:
            return 0
        if self._redis is None:
            return await self._cleanup_local()

        leader = self._redis.lock(LEADER_LOCK, timeout=55, blocking_timeout=0)
        try:
            if not await leader.acquire():
                return 0
            due = await self._redis.zrangebyscore(
                EXPIRY_ZSET, min=0, max=time.time(), start=0, num=1000
            )
            if not due:
                return 0

            locked: list[tuple[str, object, object | None]] = []
            for object_key in due:
                cache_key = await self._redis.hget(OBJECT_CACHE_KEYS, object_key)
                cache_lock = None
                if cache_key:
                    cache_lock = self._redis.lock(
                        f"{cache_key}:create",
                        timeout=120,
                        blocking_timeout=0,
                    )
                    if not await cache_lock.acquire():
                        continue
                object_lock = self._redis.lock(
                    self._object_lock_name(object_key),
                    timeout=120,
                    blocking_timeout=0,
                )
                if not await object_lock.acquire():
                    if cache_lock is not None:
                        await cache_lock.release()
                    continue
                score = await self._redis.zscore(EXPIRY_ZSET, object_key)
                if score is None or score > time.time():
                    await object_lock.release()
                    if cache_lock is not None:
                        await cache_lock.release()
                    continue
                locked.append((object_key, object_lock, cache_lock))

            keys = [key for key, _, _ in locked]
            if not keys:
                return 0
            try:
                await self.object_store.delete_many(keys)
                cache_keys = await self._redis.hmget(OBJECT_CACHE_KEYS, keys)
                current_values = await self._redis.mget(
                    [value for value in cache_keys if value]
                )
                current_by_cache = dict(
                    zip(
                        [value for value in cache_keys if value],
                        current_values,
                    )
                )
                async with self._redis.pipeline(transaction=True) as pipe:
                    pipe.zrem(EXPIRY_ZSET, *keys)
                    pipe.hdel(OBJECT_CACHE_KEYS, *keys)
                    for object_key, cache_key in zip(keys, cache_keys):
                        raw = current_by_cache.get(cache_key) if cache_key else None
                        try:
                            points_to = json.loads(raw)["object_key"] if raw else None
                        except (ValueError, KeyError, TypeError):
                            points_to = None
                        if cache_key and points_to == object_key:
                            pipe.delete(cache_key)
                    await pipe.execute()
                for object_key, cache_key in zip(keys, cache_keys):
                    if not cache_key:
                        continue
                    local_entry = self._local_entries.get(cache_key)
                    if local_entry and local_entry.object_key == object_key:
                        self._local_entries.pop(cache_key, None)
                return len(keys)
            except Exception:
                retry_at = time.time() + 60
                await self._redis.zadd(
                    EXPIRY_ZSET, {key: retry_at for key in keys}
                )
                raise
            finally:
                for _, object_lock, cache_lock in locked:
                    try:
                        await object_lock.release()
                    except RedisError:
                        pass
                    if cache_lock is not None:
                        try:
                            await cache_lock.release()
                        except RedisError:
                            pass
        except RedisError:
            logger.exception("Redis 清理索引不可用，使用进程内索引。")
            return await self._cleanup_local()
        finally:
            try:
                if await leader.owned():
                    await leader.release()
            except RedisError:
                pass

    async def _cleanup_local(self) -> int:
        now = time.time()
        async with self._local_guard:
            due = [
                (cache_key, entry)
                for cache_key, entry in self._local_entries.items()
                if entry.delete_after <= now
            ][:1000]
        if not due:
            return 0
        await self.object_store.delete_many(entry.object_key for _, entry in due)
        async with self._local_guard:
            for cache_key, entry in due:
                current = self._local_entries.get(cache_key)
                if current is entry and current.delete_after <= time.time():
                    self._local_entries.pop(cache_key, None)
        return len(due)
