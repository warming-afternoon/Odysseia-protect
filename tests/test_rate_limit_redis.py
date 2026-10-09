"""可选真实 Redis 验收，通过 REDIS_TEST_URL 显式指定测试实例。"""

import asyncio
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError

from src.dto.rate_limit_config import RateLimitConfig
from src.qo.rate_limit_qo import RateLimitQo
from src.services.rate_limit_service import RateLimitService

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("REDIS_TEST_URL"), reason="未配置独立的 REDIS_TEST_URL"
    ),
]


async def test_atomic_redis_admission_and_recovery_after_state_loss():
    """验证跨实例原子额度和 Redis 状态丢失后的本地镜像合并。"""
    user_id = uuid4().int
    first = RateLimitService(redis_url=os.environ["REDIS_TEST_URL"])
    second = RateLimitService(redis_url=os.environ["REDIS_TEST_URL"])
    prefix = f"odysseia-protect:rate-limit:{{{user_id}}}"
    keys = [f"{prefix}:minute", f"{prefix}:daily", f"{prefix}:watch"]
    await first.start()
    await second.start()
    try:
        assert first._mode == second._mode == "redis"
        results = await asyncio.gather(
            *(
                (first if index % 2 else second).check(RateLimitQo(user_id))
                for index in range(100)
            )
        )
        assert sum(result.allowed for result in results) == 30
        snapshot = await first.check(RateLimitQo(user_id, charge=False))
        assert snapshot.daily_count == snapshot.minute_count == 30 and snapshot.watched
        for key in keys:
            assert await first._redis.ttl(key) > 0

        # 仅清理本测试随机用户的键，模拟 Redis 状态丢失。
        redis = first._redis
        await redis.delete(*keys)
        offline = AsyncMock()
        offline.eval.side_effect = ConnectionError()
        first._redis = offline
        denied = await first.check(RateLimitQo(user_id))
        assert not denied.allowed and denied.daily_count == 30
        first._redis = redis
        recovered = await first.check(RateLimitQo(user_id))
        assert recovered.mode == "redis" and not recovered.allowed
        assert recovered.daily_count == recovered.minute_count == 30
    finally:
        if first._redis is not None:
            await first._redis.delete(*keys)
        await first.close()
        await second.close()


async def test_atomic_redis_daily_watch_and_daily_limit():
    """验证 Lua 脚本在第一百零一次观察和第四百零一次拒绝。"""
    user_id = uuid4().int
    service = RateLimitService(
        RateLimitConfig(max_requests=1000), redis_url=os.environ["REDIS_TEST_URL"]
    )
    prefix = f"odysseia-protect:rate-limit:{{{user_id}}}"
    keys = [f"{prefix}:minute", f"{prefix}:daily", f"{prefix}:watch"]
    await service.start()
    try:
        assert service._mode == "redis"
        for count in range(1, 401):
            result = await service.check(RateLimitQo(user_id))
            assert result.allowed and result.daily_count == count
            if count == 100:
                assert not result.watched
            if count >= 101:
                assert result.watched
        denied = await service.check(RateLimitQo(user_id))
        assert not denied.allowed and denied.daily_count == 400
        assert "daily_rate_limit" in denied.reasons
    finally:
        if service._redis is not None:
            await service._redis.delete(*keys)
        await service.close()
