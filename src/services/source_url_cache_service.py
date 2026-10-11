"""Discord 源附件 URL 的共享缓存与原图读取。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager

import aiohttp
from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.dto.source_url_cache_entry import SourceUrlCacheEntry
from src.dto.source_url_cache_in_flight import SourceUrlCacheInFlight
from src.utils.source_url_cache_ttl import SourceUrlCacheTtl

logger = logging.getLogger(__name__)
LOCK_TIMEOUT_SECONDS = 90
"""分布式回源锁的租期和最长等待时间，单位为秒。"""

_REDIS_ERRORS = (RedisError, OSError, asyncio.TimeoutError)
"""触发本地缓存降级的 Redis 操作、网络连接及超时异常类型。"""

# 失效与写入在 Redis 内原子串行，阻止消息删除后旧回源结果重新落入缓存。
_INVALIDATE_SCRIPT = """
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
return redis.call('DEL', KEYS[1])
"""
"""原子更新失效版本并删除缓存，阻止尚未完成的旧回源重新写入。"""

_WRITE_SCRIPT = """
local version = redis.call('GET', KEYS[2]) or ''
if version ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""
"""仅在失效版本匹配时写入链接及缓存期限，拒绝失效前的回源结果。"""


class SourceUrlCacheService:
    """管理源附件链接的共享缓存、失效同步和原图下载。"""

    def __init__(
        self, *, redis_url: str | None = None,
        clock: Callable[[], float] = time.time, max_entries: int = 10000,
    ):
        """初始化源链接缓存配置和运行状态。"""
        # 本地镜像按绝对时间过期，容量上限用于限制进程内存占用。
        self.redis_url = os.getenv("REDIS_URL") if redis_url is None else redis_url
        self._clock = clock
        self._max_entries = max_entries
        self._redis: Redis | None = None
        self._mode: str | None = None
        self._entries: OrderedDict[str, SourceUrlCacheEntry] = OrderedDict()
        self._in_flight: dict[str, SourceUrlCacheInFlight] = {}
        self._session: aiohttp.ClientSession | None = None

    def _set_mode(self, mode: str) -> None:
        """仅在缓存后端模式变化时记录降级或恢复。"""
        # 相同状态不重复输出故障日志，避免 Redis 中断时产生大量告警。
        if mode == self._mode:
            return
        self._mode = mode
        if mode == "local":
            logger.warning("源附件 URL 缓存退化为进程内模式，多实例失效暂时无法同步。")
        else:
            logger.info("源附件 URL 缓存已连接/恢复 Redis。")

    async def start(self) -> None:
        """建立 Redis 连接和原图下载会话。"""
        # Redis 短超时可在服务不可用时及时切换到本地缓存。
        if self.redis_url and self._redis is None:
            self._redis = Redis.from_url(
                self.redis_url, decode_responses=True, protocol=2,
                socket_connect_timeout=1, socket_timeout=1,
            )
        if self._redis is not None:
            try:
                await self._redis.ping()
                self._set_mode("redis")
            except _REDIS_ERRORS:
                self._set_mode("local")
        else:
            self._set_mode("local")
        # 下载会话在服务生命周期内共享，以复用 HTTP 连接。
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))

    async def close(self) -> None:
        """关闭原图会话和 Redis 连接并释放本地缓存。"""
        # 即使原图会话关闭失败，也尝试释放 Redis 自有连接。
        try:
            if self._session is not None:
                await self._session.close()
                self._session = None
        finally:
            if self._redis is not None:
                await self._redis.aclose()
                self._redis = None
            self._entries.clear()

    def _read_local(self, key: str) -> SourceUrlCacheEntry | None:
        """读取未过期的本地镜像并更新访问顺序。"""
        # 本地镜像按绝对期限失效，读取时仅调整淘汰顺序。
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.cache_until <= self._clock():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return entry

    def _decode_entry(self, raw: str | None) -> SourceUrlCacheEntry | None:
        """解析共享缓存载荷并校验链接类型及绝对到期时间。"""
        # 缺失、损坏或过期的共享记录均视为未命中。
        if not raw:
            return None
        try:
            value = json.loads(raw)
            entry = SourceUrlCacheEntry(value["url"], float(value["cache_until"]))
        except (KeyError, ValueError, TypeError):
            return None
        if not isinstance(entry.url, str):
            return None
        if not math.isfinite(entry.cache_until) or entry.cache_until <= self._clock():
            return None
        return entry

    async def _read(self, key: str) -> SourceUrlCacheEntry | None:
        """优先读取共享缓存，故障时使用未过期的本地镜像。"""
        # 每次读取都尝试 Redis，让恢复后的共享失效状态优先于本地旧数据。
        if self._redis is None:
            return self._read_local(key)
        try:
            raw = await self._redis.get(key)
        except _REDIS_ERRORS:
            self._set_mode("local")
            return self._read_local(key)
        self._set_mode("redis")
        entry = self._decode_entry(raw)
        # 共享记录缺失或不可用时清除旧镜像，避免复用其他实例已失效的链接。
        if entry is None:
            self._entries.pop(key, None)
            return None
        # 有效共享记录同步到本地镜像，并按容量淘汰最久未访问项。
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return entry

    @asynccontextmanager
    async def _local_lock(self, key: str) -> AsyncIterator[SourceUrlCacheInFlight]:
        """合并同一进程的并发回源并维护失效版本。"""
        # 引用计数同时覆盖持锁与等待请求，最后一个请求结束后移除锁。
        state = self._in_flight.setdefault(key, SourceUrlCacheInFlight())
        state.users += 1
        try:
            async with state.lock:
                yield state
        finally:
            state.users -= 1
            if not state.users:
                self._in_flight.pop(key, None)

    @asynccontextmanager
    async def _shared_lock(self, key: str) -> AsyncIterator[bool]:
        """合并跨实例回源，Redis 故障时沿用进程内互斥。"""
        # 本地模式不再等待分布式锁，避免一次请求反复承受连接超时。
        if self._redis is None or self._mode == "local":
            yield False
            return
        lock = self._redis.lock(
            key, timeout=LOCK_TIMEOUT_SECONDS, blocking_timeout=LOCK_TIMEOUT_SECONDS,
        )
        try:
            acquired = await lock.acquire()
        except _REDIS_ERRORS:
            self._set_mode("local")
            yield False
            return
        if not acquired:
            raise TimeoutError("等待源附件 URL 缓存锁超时。")
        try:
            yield True
        finally:
            try:
                await lock.release()
            except _REDIS_ERRORS:
                self._set_mode("local")

    async def get_url(
        self, channel_id: int, message_id: int, loader: Callable[[], Awaitable[str]],
    ) -> str:
        """按源消息获取附件链接，并缓存仍有足够有效期的回源结果。"""
        # 同一源消息的缓存、版本和锁使用同一个 Redis 哈希槽。
        prefix = f"odysseia-protect:source-url:v1:{{{channel_id}:{message_id}}}"
        key, version_key, lock_key = f"{prefix}:data", f"{prefix}:version", f"{prefix}:lock"
        entry = await self._read(key)
        if entry is not None:
            logger.debug("源附件 URL 缓存命中：%s", key)
            return entry.url
        async with (
            self._local_lock(key) as state,
            self._shared_lock(lock_key) as shared,
        ):
            # 等待锁的请求再次检查缓存，复用前一个请求的回源结果。
            entry = await self._read(key)
            if entry is not None:
                return entry.url
            return await self._load_and_cache(key, version_key, state, shared, loader)

    async def _load_and_cache(
        self, key: str, version_key: str, state: SourceUrlCacheInFlight,
        shared: bool, loader: Callable[[], Awaitable[str]],
    ) -> str:
        """在持锁期间回源并按失效版本校验结果后写入缓存。"""
        # 先记录失效版本，拒绝缓存主动失效前启动的旧回源结果。
        local_version = state.version
        redis_version = None
        if shared and self._mode == "redis":
            try:
                redis_version = await self._redis.get(version_key) or ""
            except _REDIS_ERRORS:
                self._set_mode("local")
        logger.debug("源附件 URL 缓存未命中：%s", key)
        # 回源的最长时间小于分布式锁租期，避免慢请求在锁过期后重复写入。
        async with asyncio.timeout(LOCK_TIMEOUT_SECONDS - 5):
            url = await loader()
        if state.version != local_version:
            raise ValueError("源附件已变更，请重新下载。")
        now = self._clock()
        ttl = SourceUrlCacheTtl.cache_ttl(url, now)
        if ttl <= 0:
            logger.debug("源附件 URL 剩余有效期不足或未知，跳过缓存：%s", key)
            return url
        entry = SourceUrlCacheEntry(url, now + ttl)
        # 原子比较共享失效版本后写入；网络故障时保留本地缓存能力。
        if redis_version is not None:
            try:
                saved = await self._redis.eval(
                    _WRITE_SCRIPT, 2, key, version_key, redis_version,
                    json.dumps({"url": url, "cache_until": entry.cache_until}), ttl,
                )
            except _REDIS_ERRORS:
                self._set_mode("local")
            else:
                if not saved:
                    raise ValueError("源附件已变更，请重新下载。")
        if state.version != local_version:
            raise ValueError("源附件已变更，请重新下载。")
        # 本地副本使用与 Redis 记录一致的绝对期限，避免延长链接缓存寿命。
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return url

    async def invalidate(self, channel_id: int, message_ids: Iterable[int]) -> None:
        """清理指定消息的源链接缓存，并阻止并发请求写回旧链接。"""
        # 对消息 ID 去重，减少同一批失效操作中的重复缓存指令。
        keys = []
        for message_id in set(message_ids):
            prefix = f"odysseia-protect:source-url:v1:{{{channel_id}:{message_id}}}"
            keys.append((f"{prefix}:data", f"{prefix}:version", f"{prefix}:lock"))
        # 本地失效不等待网络，同时更新尚未结束的回源请求版本。
        for key, _, _ in keys:
            self._entries.pop(key, None)
            state = self._in_flight.get(key)
            if state is not None:
                state.version += 1
        logger.debug("源附件 URL 缓存失效：频道 %s，消息数 %s", channel_id, len(keys))
        if not keys or self._redis is None:
            return
        # 每条消息的版本更新和缓存删除在 Redis 内原子执行。
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for key, version_key, _ in keys:
                    pipe.eval(
                        _INVALIDATE_SCRIPT, 2, key, version_key, uuid.uuid4().hex,
                        SourceUrlCacheTtl.CACHE_TTL_SECONDS + LOCK_TIMEOUT_SECONDS,
                    )
                await pipe.execute()
            self._set_mode("redis")
        except _REDIS_ERRORS:
            self._set_mode("local")

    async def read_source(
        self, channel_id: int, message_id: int, loader: Callable[[], Awaitable[str]],
    ) -> bytes:
        """通过缓存链接读取原图，签名失效时刷新并重试一次。"""
        # 原图下载依赖服务启动时创建的共享会话。
        if self._session is None:
            raise RuntimeError("源附件 URL 缓存服务尚未启动。")
        # 首次遭遇拒绝访问或附件缺失时失效缓存，第二次失败直接返回错误。
        for attempt in range(2):
            url = await self.get_url(channel_id, message_id, loader)
            async with self._session.get(url) as response:
                if response.status == 200:
                    return await response.read()
                refresh = response.status in (403, 404)
                if refresh:
                    await self.invalidate(channel_id, [message_id])
                if not refresh or attempt == 1:
                    # 错误信息仅保留状态码，以保护下载链接中的访问签名。
                    raise RuntimeError(f"源附件读取失败，HTTP {response.status}。")
                logger.debug("源附件 CDN 拒绝访问，刷新 URL 重试：%s/%s", channel_id, message_id)
        raise RuntimeError("源附件读取失败。")
