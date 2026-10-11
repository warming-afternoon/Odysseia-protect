"""源 URL 缓存：期限、并发失效、故障降级和原图重试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from redis.exceptions import ConnectionError

from src.dto.resource_dto import ResourceDTO
from src.enums import UploadMode
from src.services.download_service import DownloadService
from src.services.management_service import ManagementService
from src.services.source_url_cache_service import SourceUrlCacheService
from src.utils.source_url_cache_ttl import SourceUrlCacheTtl
from tests.source_loader import SourceLoader

NOW = 1800000000
"""缓存期限测试使用的固定 Unix 时间戳，单位为秒。"""


URL_TEMPLATE = 'https://cdn.discordapp.com/attachments/1/2/{name}.png?ex={expiry:x}&is=1&hm=signature'
"""可指定文件名和十六进制到期时间的模拟附件签名链接模板。"""

SIGNED_URL = URL_TEMPLATE.format(name='card', expiry=NOW + 86400)
"""有效期为二十四小时的模拟源附件链接。"""


@pytest.mark.parametrize('remaining, expected', [
    (86400, 21600), (21601, 21600), (21600, 21600),
    (21599, 17999), (18000, 14400), (3601, 1), (3600, 0),
    (3599, 0), (0, 0), (-1, 0),
])
def test_ttl_boundaries(remaining, expected):
    """核对六小时与一小时边界上的缓存期限。"""
    assert SourceUrlCacheTtl.cache_ttl(URL_TEMPLATE.format(name='card', expiry=NOW + remaining), NOW) == expected


@pytest.mark.parametrize('query', ['', '?ex=', '?ex=invalid', '?ex=1', '?ex=-1'])
def test_unknown_or_expired_expiry_is_not_cached(query):
    """确认未知或过期签名不会写入缓存。"""
    assert SourceUrlCacheTtl.cache_ttl('https://cdn.discordapp.com/a.png' + query, NOW) == 0


def test_ttl_rounds_down_fractional_seconds():
    """确认缓存期限向下取整且不产生负数。"""
    assert SourceUrlCacheTtl.cache_ttl(URL_TEMPLATE.format(name='card', expiry=NOW + 3601), NOW + 0.1) == 0


@pytest.mark.asyncio
async def test_local_hit_does_not_slide_and_keys_include_channel_and_message():
    """确认本地命中不续期且频道与消息缓存相互隔离。"""
    now = [NOW]
    cache = SourceUrlCacheService(redis_url='', clock=lambda: now[0])
    loader = AsyncMock(return_value=SIGNED_URL)
    await cache.get_url(1, 2, loader)
    now[0] += 21599
    await cache.get_url(1, 2, loader)
    assert loader.await_count == 1
    now[0] += 1
    await cache.get_url(1, 2, loader)
    assert loader.await_count == 2
    await cache.get_url(3, 2, loader)
    await cache.get_url(1, 4, loader)
    assert loader.await_count == 4


@pytest.mark.asyncio
async def test_invalid_url_and_loader_error_never_cached():
    """确认无有效期限或回源失败的链接不会进入缓存。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW)
    loader = AsyncMock(return_value='https://cdn.discordapp.com/a.png')
    await cache.get_url(1, 2, loader)
    await cache.get_url(1, 2, loader)
    assert loader.await_count == 2
    loader.side_effect = ValueError('no attachments')
    with pytest.raises(ValueError):
        await cache.get_url(1, 2, loader)
    assert not cache._entries
    assert not cache._in_flight


@pytest.mark.asyncio
async def test_local_concurrent_requests_load_once_and_locks_are_pruned():
    """确认进程内并发只回源一次并在结束后释放锁。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW)

    # 模拟网络等待，让后续请求确实进入同一键的等待队列。
    loader = SourceLoader(SIGNED_URL, delay=0.01)
    assert await asyncio.gather(*(cache.get_url(1, 2, loader) for _ in range(30))) == [SIGNED_URL] * 30
    assert loader.calls == 1
    assert not cache._in_flight


@pytest.mark.asyncio
async def test_local_invalidation_fences_in_flight_response():
    """确认本地失效阻止尚未完成的旧回源写入。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW)
    entered, resume = asyncio.Event(), asyncio.Event()

    # 暂停回源，先执行失效，再允许旧请求返回。
    loader = SourceLoader(SIGNED_URL, entered=entered, resume=resume)
    pending = asyncio.create_task(cache.get_url(1, 2, loader))
    await entered.wait()
    await cache.invalidate(1, [2])
    resume.set()
    with pytest.raises(ValueError, match='已变更'):
        await pending
    assert not cache._entries
    assert not cache._in_flight


@pytest.mark.asyncio
async def test_lru_eviction_and_bulk_local_invalidation():
    """确认容量淘汰遵循访问顺序且支持批量失效。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW, max_entries=2)
    loader = AsyncMock(return_value=SIGNED_URL)
    await cache.get_url(1, 2, loader)
    await cache.get_url(1, 3, loader)
    await cache.get_url(1, 2, loader)
    await cache.get_url(1, 4, loader)
    assert len(cache._entries) == 2
    assert 'odysseia-protect:source-url:v1:{1:3}:data' not in cache._entries
    await cache.invalidate(1, [2, 4, 4])
    assert not cache._entries


@pytest.mark.asyncio
async def test_redis_outage_uses_local_cache_and_recovery_ignores_old_mirror():
    """确认故障时使用本地镜像且恢复后以共享状态为准。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW)
    loader = AsyncMock(return_value=SIGNED_URL)
    await cache.get_url(1, 2, loader)
    redis = AsyncMock()
    redis.get.side_effect = ConnectionError()
    cache._redis = redis
    assert await cache.get_url(1, 2, loader) == SIGNED_URL
    assert cache._mode == 'local'
    loader.assert_awaited_once()
    redis.get.side_effect = None
    redis.get.return_value = None
    # Redis 恢复后的 miss 不复用本地旧副本。
    assert await cache._read('odysseia-protect:source-url:v1:{1:2}:data') is None
    assert not cache._entries
    assert cache._mode == 'redis'


@pytest.mark.asyncio
async def test_redis_invalidation_failure_still_clears_local_entries():
    """确认共享失效失败时仍然清除本地副本。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW)
    await cache.get_url(1, 2, AsyncMock(return_value=SIGNED_URL))
    pipe = MagicMock()
    pipe.execute = AsyncMock(side_effect=ConnectionError())
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=None)
    cache._redis = MagicMock(pipeline=MagicMock(return_value=pipe))
    await cache.invalidate(1, [2])
    assert not cache._entries
    assert cache._mode == 'local'


def session_with_responses(*statuses):
    """创建按顺序返回指定状态码的原图请求会话。"""
    # 每个响应都有独立的异步读取记录，便于核对失败响应是否读取了正文。
    responses = [SimpleNamespace(status=s, read=AsyncMock(return_value=b'png')) for s in statuses]
    session = SimpleNamespace(get=MagicMock())
    contexts = []
    for response in responses:
        # 每次请求绑定独立响应上下文，用于验证重试顺序和正文读取次数。
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=None)
        contexts.append(context)
    session.get.side_effect = contexts
    return session, responses


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [403, 404])
async def test_source_read_refreshes_on_403_or_404_once(status):
    """确认原图拒绝访问或缺失时刷新链接并重试一次。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW)
    cache._session, responses = session_with_responses(status, 200)
    loader = AsyncMock(side_effect=[URL_TEMPLATE.format(name='old', expiry=NOW + 86400), URL_TEMPLATE.format(name='new', expiry=NOW + 86400)])
    assert await cache.read_source(1, 2, loader) == b'png'
    assert loader.await_count == 2
    assert cache._session.get.call_count == 2
    responses[0].read.assert_not_awaited()
    responses[1].read.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('statuses, expected_loads', [([404, 404], 2), ([500], 1)])
async def test_source_read_retry_is_bounded_and_other_errors_do_not_retry(statuses, expected_loads):
    """确认原图刷新重试有界且其他错误直接返回。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW)
    cache._session, _ = session_with_responses(*statuses)
    loader = AsyncMock(return_value=SIGNED_URL)
    with pytest.raises(RuntimeError, match='HTTP') as exc:
        await cache.read_source(1, 2, loader)
    assert 'hm=' not in str(exc.value)
    assert loader.await_count == expected_loads


@pytest.mark.asyncio
async def test_download_and_source_bytes_share_cache():
    """确认下载取链与溯源原图读取复用同一源缓存。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW)
    cache._session, _ = session_with_responses(200)
    channel = MagicMock(spec=discord.Thread)
    channel.fetch_message = AsyncMock(return_value=SimpleNamespace(attachments=[SimpleNamespace(url=SIGNED_URL)]))
    bot = SimpleNamespace(source_url_cache_service=cache, fetch_channel=AsyncMock(return_value=channel))
    service = DownloadService(bot, None, None, None)
    resource = ResourceDTO(id=1, public_thread_id=10, warehouse_thread_id=20, source_message_id=2,
                           upload_mode=UploadMode.SECURE, trace_enabled=True)
    assert await service.fetch_fresh_url(resource) == SIGNED_URL
    assert await service.fetch_source_bytes(resource) == b'png'
    bot.fetch_channel.assert_awaited_once_with(20)
    channel.fetch_message.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_normal_source_uses_public_thread_and_missing_attachment_is_not_cached():
    """确认普通资源读取公开帖子且缺失附件不写缓存。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW)
    channel = MagicMock(spec=discord.Thread)
    channel.fetch_message = AsyncMock(return_value=SimpleNamespace(attachments=[]))
    bot = SimpleNamespace(source_url_cache_service=cache, fetch_channel=AsyncMock(return_value=channel))
    service = DownloadService(bot, None, None, None)
    resource = ResourceDTO(id=1, public_thread_id=10, warehouse_thread_id=20, source_message_id=2,
                           upload_mode=UploadMode.NORMAL)
    with pytest.raises(ValueError, match='没有附件'):
        await service.fetch_fresh_url(resource)
    bot.fetch_channel.assert_awaited_once_with(10)
    assert not cache._entries


@pytest.mark.asyncio
@pytest.mark.parametrize('mode, source_error', [
    (UploadMode.SECURE, None), (UploadMode.SECURE, 'not_found'),
    (UploadMode.SECURE, 'forbidden'), (UploadMode.NORMAL, None),
])
async def test_resource_deletion_actively_invalidates_without_gateway_event(mode, source_error):
    """确认主动资源删除不依赖消息删除事件清理缓存。"""
    cache = SimpleNamespace(invalidate=AsyncMock())
    channel = MagicMock(spec=discord.Thread)
    message = SimpleNamespace(delete=AsyncMock())
    channel.fetch_message = AsyncMock(return_value=message)
    if source_error:
        error_type = discord.NotFound if source_error == 'not_found' else discord.Forbidden
        channel.fetch_message.side_effect = error_type(SimpleNamespace(status=404, reason='gone'), 'gone')
    bot = SimpleNamespace(source_url_cache_service=cache, fetch_channel=AsyncMock(return_value=channel))
    resource = SimpleNamespace(upload_mode=mode, source_message_id=2,
                               thread=SimpleNamespace(warehouse_thread_id=20, public_thread_id=10))
    repo = SimpleNamespace(get_with_thread=AsyncMock(return_value=resource), remove=AsyncMock(return_value=resource))
    service = ManagementService(bot, repo, None, None)
    with patch('src.services.management_service.require_thread_manager', AsyncMock()):
        assert await service.delete_resource(AsyncMock(), resource_id=1, interaction=MagicMock())
    cache.invalidate.assert_awaited_once_with(20 if mode == UploadMode.SECURE else 10, [2])
    if mode == UploadMode.NORMAL:
        bot.fetch_channel.assert_not_awaited()
    elif source_error is None:
        message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_authorization_does_not_invalidate_cache():
    """确认权限检查失败时不会清理源缓存。"""
    cache = SimpleNamespace(invalidate=AsyncMock())
    repo = SimpleNamespace(get_with_thread=AsyncMock(return_value=SimpleNamespace(thread=object())))
    service = ManagementService(SimpleNamespace(source_url_cache_service=cache), repo, None, None)
    with patch('src.services.management_service.require_thread_manager', AsyncMock(side_effect=PermissionError())):
        with pytest.raises(PermissionError):
            await service.delete_resource(AsyncMock(), resource_id=1, interaction=MagicMock())
    cache.invalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_service_lifecycle_closes_session_and_redis():
    """确认服务重复启动和关闭时正确释放自有连接。"""
    cache = SourceUrlCacheService(redis_url='')
    await cache.start()
    session = cache._session
    assert session.timeout.total == 60
    await cache.start()
    assert cache._session is session
    redis = AsyncMock()
    cache._redis = redis
    await cache.close()
    assert session.closed
    redis.aclose.assert_awaited_once()
    assert cache._session is cache._redis is None
    await cache.close()


@pytest.mark.asyncio
async def test_delete_failure_after_source_removal_still_invalidates():
    """确认源消息已删除后数据库失败仍会清理链接缓存。"""
    cache = SimpleNamespace(invalidate=AsyncMock())
    channel = MagicMock(spec=discord.Thread)
    message = SimpleNamespace(delete=AsyncMock())
    channel.fetch_message = AsyncMock(return_value=message)
    resource = SimpleNamespace(upload_mode=UploadMode.SECURE, source_message_id=2,
                               thread=SimpleNamespace(warehouse_thread_id=20, public_thread_id=10))
    repo = SimpleNamespace(get_with_thread=AsyncMock(return_value=resource),
                           remove=AsyncMock(side_effect=RuntimeError('DB failure')))
    bot = SimpleNamespace(source_url_cache_service=cache, fetch_channel=AsyncMock(return_value=channel))
    service = ManagementService(bot, repo, None, None)
    with patch('src.services.management_service.require_thread_manager', AsyncMock()):
        with pytest.raises(RuntimeError, match='DB failure'):
            await service.delete_resource(AsyncMock(), resource_id=1, interaction=MagicMock())
    message.delete.assert_awaited_once()
    cache.invalidate.assert_awaited_once_with(20, [2])


@pytest.mark.asyncio
async def test_replace_source_invalidates_old_url_after_commit():
    """确认换源在事务提交后清理旧消息缓存。"""
    import io
    cache = SimpleNamespace(invalidate=AsyncMock())
    channel = MagicMock(spec=discord.Thread)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=3))
    channel.fetch_message = AsyncMock(return_value=SimpleNamespace(delete=AsyncMock()))
    resource = SimpleNamespace(upload_mode=UploadMode.SECURE, source_message_id=2, trace_enabled=False,
                               thread=SimpleNamespace(id=1, warehouse_thread_id=20))
    repo = SimpleNamespace(get_with_thread=AsyncMock(return_value=resource))
    bot = SimpleNamespace(source_url_cache_service=cache, fetch_channel=AsyncMock(return_value=channel))
    service = ManagementService(bot, repo, None, None)
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(rowcount=1)
    attachment = SimpleNamespace(title=None, filename='new.png',
                                 to_file=AsyncMock(return_value=discord.File(io.BytesIO(b'png'), filename='new.png')))
    calls = []
    session.commit.side_effect = lambda: calls.append('commit')
    cache.invalidate.side_effect = lambda *_: calls.append('invalidate')
    with patch('src.services.management_service.require_thread_manager', AsyncMock()):
        await service.replace_resource_source(
            session, resource_id=1, interaction=MagicMock(), attachment=attachment,
            expected_source_message_id=2,
        )
    assert calls == ['commit', 'invalidate']
    cache.invalidate.assert_awaited_once_with(20, [2])


@pytest.mark.asyncio
async def test_cached_link_remains_available_until_ttl_after_source_disappears():
    """确认源消息不可用后仍复用未过期链接，缓存到期后报告回源失败。"""
    now = [NOW]
    cache = SourceUrlCacheService(redis_url='', clock=lambda: now[0])
    loader = AsyncMock(return_value=SIGNED_URL)
    await cache.get_url(1, 2, loader)
    # 模拟源消息已删除，后续消息查询无法再取得附件链接。
    loader.side_effect = ValueError('源消息不存在')
    now[0] += 21599
    assert await cache.get_url(1, 2, loader) == SIGNED_URL
    assert loader.await_count == 1
    # 固定期限到达后必须重新查询源消息，不能延长已有缓存的寿命。
    now[0] += 1
    with pytest.raises(ValueError, match='源消息不存在'):
        await cache.get_url(1, 2, loader)
    assert loader.await_count == 2
    assert not cache._entries


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [
    None, '', 'invalid json', 'null', '[]', '{}',
    '{"url": 1, "cache_until": 1800086400}',
    '{"url": "signed", "cache_until": "invalid"}',
    '{"url": "signed", "cache_until": null}',
    '{"url": "signed", "cache_until": "NaN"}',
    '{"url": "signed", "cache_until": "Infinity"}',
    '{"url": "signed", "cache_until": 1800000000}',
])
async def test_invalid_shared_record_removes_local_mirror(raw):
    """确认缺失、损坏或过期的共享记录清除本地镜像而不复用旧链接。"""
    cache = SourceUrlCacheService(redis_url='', clock=lambda: NOW)
    await cache.get_url(1, 2, AsyncMock(return_value=SIGNED_URL))
    # Redis 恢复后的无效记录必须覆盖进程内的有效旧副本。
    cache._redis = AsyncMock()
    cache._redis.get.return_value = raw
    key = 'odysseia-protect:source-url:v1:{1:2}:data'
    assert await cache._read(key) is None
    assert not cache._entries
    assert cache._mode == 'redis'
