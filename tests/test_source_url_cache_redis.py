"""通过 REDIS_TEST_URL 对独立 Redis 实例进行源 URL 缓存验收。"""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.source_url_cache_service import SourceUrlCacheService
from tests.source_loader import SourceLoader

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    not os.getenv('REDIS_TEST_URL'), reason='未配置独立的 REDIS_TEST_URL',
)]


@asynccontextmanager
async def caches():
    """启动两个缓存实例并在验收结束后释放测试键和连接。"""
    channel_id, message_id = uuid4().int, uuid4().int
    first = SourceUrlCacheService(redis_url=os.environ['REDIS_TEST_URL'])
    second = SourceUrlCacheService(redis_url=os.environ['REDIS_TEST_URL'])
    try:
        await first.start()
        await second.start()
        assert first._mode == second._mode == 'redis'
        yield first, second, channel_id, message_id
    finally:
        try:
            if first._redis is not None and first._mode == 'redis':
                await first._redis.delete(*tuple(
                    f'odysseia-protect:source-url:v1:{{{channel_id}:{message_id}}}:{suffix}'
                    for suffix in ('data', 'version', 'lock')
                ))
        finally:
            await first.close()
            await second.close()


URL_TEMPLATE = 'https://cdn.discordapp.com/a.png?ex={expiry:x}&hm=test'
"""Redis 验收使用的模拟签名链接模板，到期时间由各用例指定。"""


async def test_cross_instance_hit_has_six_hour_ttl_without_renewal():
    """确认跨实例缓存共享六小时期限且命中不续期。"""
    async with caches() as (first, second, channel, message):
        loader = AsyncMock(return_value=URL_TEMPLATE.format(expiry=int(time.time()) + 24 * 3600))
        expected = await first.get_url(channel, message, loader)
        key = f'odysseia-protect:source-url:v1:{{{channel}:{message}}}:data'
        assert 21590 <= await first._redis.ttl(key) <= 21600
        # 缩短 Redis TTL，命中不得续期。
        await first._redis.expire(key, 60)
        assert await second.get_url(channel, message, loader) == expected
        assert 0 < await first._redis.ttl(key) <= 60
        loader.assert_awaited_once()


async def test_short_remaining_ttl_and_stored_deadline_are_enforced():
    """确认短签名预留一小时且绝对缓存期限生效。"""
    async with caches() as (first, second, channel, message):
        loader = AsyncMock(return_value=URL_TEMPLATE.format(expiry=int(time.time()) + 5 * 3600))
        await first.get_url(channel, message, loader)
        key = f'odysseia-protect:source-url:v1:{{{channel}:{message}}}:data'
        assert 14390 <= await first._redis.ttl(key) <= 14400
        # 即使 Redis TTL 尚在，载荷中的绝对缓存期限已过也必须回源。
        await first._redis.set(key, json.dumps({'url': URL_TEMPLATE.format(expiry=int(time.time()) + 24 * 3600), 'cache_until': time.time() - 1}), ex=60)
        await second.get_url(channel, message, loader)
        assert loader.await_count == 2


async def test_cross_instance_cold_requests_fetch_once():
    """确认跨实例并发未命中仅执行一次回源。"""
    async with caches() as (first, second, channel, message):
        expected = URL_TEMPLATE.format(expiry=int(time.time()) + 24 * 3600)

        # 延迟回源结果，让两个实例在 Redis 锁上发生实际竞争。
        loader = SourceLoader(expected, delay=0.05)
        results = await asyncio.gather(*(
            (first if i % 2 else second).get_url(channel, message, loader) for i in range(30)
        ))
        assert results == [expected] * 30
        assert loader.calls == 1


async def test_other_instance_invalidation_overrides_local_mirror():
    """确认其他实例的共享失效优先于本地旧副本。"""
    async with caches() as (first, second, channel, message):
        loader = AsyncMock(return_value=URL_TEMPLATE.format(expiry=int(time.time()) + 24 * 3600))
        await first.get_url(channel, message, loader)
        await second.get_url(channel, message, loader)
        await second.invalidate(channel, [message])
        key = f'odysseia-protect:source-url:v1:{{{channel}:{message}}}:data'
        assert await first._redis.get(key) is None
        await first.get_url(channel, message, loader)
        assert loader.await_count == 2


async def test_cross_instance_deletion_fences_old_fetch():
    """确认跨实例删除阻止旧回源重新写入缓存。"""
    async with caches() as (first, second, channel, message):
        entered, resume = asyncio.Event(), asyncio.Event()

        # 第一个实例的回源暂停期间，由第二个实例执行失效。
        loader = SourceLoader(
            URL_TEMPLATE.format(expiry=int(time.time()) + 24 * 3600),
            entered=entered, resume=resume,
        )
        pending = asyncio.create_task(first.get_url(channel, message, loader))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            await second.invalidate(channel, [message])
        finally:
            resume.set()
        with pytest.raises(ValueError, match='已变更'):
            await pending
        prefix = f'odysseia-protect:source-url:v1:{{{channel}:{message}}}'
        key, version_key = f'{prefix}:data', f'{prefix}:version'
        assert await first._redis.get(key) is None
        assert await first._redis.get(version_key)
        assert not first._entries
