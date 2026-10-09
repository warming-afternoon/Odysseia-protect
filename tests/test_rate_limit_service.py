"""共享限流的边界、并发、观察与后端切换验收。"""

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError

from src.dto.rate_limit_config import RateLimitConfig
from src.qo.rate_limit_qo import RateLimitQo
from src.services.rate_limit_service import RateLimitService
from src.utils.beijing_time import BEIJING, day_window


def make_service(**config):
    """构建带可控北京时间时钟的本地限流服务。"""
    clock = SimpleNamespace(now=datetime(2026, 10, 9, 12, tzinfo=BEIJING).timestamp())
    service = RateLimitService(
        RateLimitConfig(**config), redis_url="", clock=lambda: clock.now
    )
    return service, clock


@pytest.mark.asyncio
async def test_minute_boundary_user_isolation_and_rejected_requests_do_not_charge():
    """验证第三十次获准、第三十一次拒绝及窗口恢复。"""
    service, clock = make_service()
    for expected in range(1, 31):
        result = await service.check(RateLimitQo(123))
        assert result.allowed and result.minute_count == expected
    for _ in range(10):
        result = await service.check(RateLimitQo(123))
        assert not result.allowed
        assert result.minute_count == result.daily_count == 30
        assert result.reset_after == 60
        assert result.reasons == ("minute_rate_limit",)
    assert (await service.check(RateLimitQo(456))).allowed
    clock.now += 59.1
    assert (await service.check(RateLimitQo(123))).reset_after == 1
    clock.now += 0.9
    result = await service.check(RateLimitQo(123))
    assert result.allowed and result.minute_count == 1 and result.daily_count == 31


@pytest.mark.asyncio
async def test_daily_watch_and_hard_limit_and_beijing_midnight():
    """验证日观察和拦截边界，以及北京时间跨日重置。"""
    service, clock = make_service()
    for count in range(1, 401):
        if count > 1 and (count - 1) % 30 == 0:
            clock.now += 60
        result = await service.check(RateLimitQo(123))
        assert result.allowed and result.daily_count == count
        if count == 100:
            assert not result.watched
        if count >= 101:
            assert result.watched and result.reasons == ("daily_watch",)
    result = await service.check(RateLimitQo(123))
    assert not result.allowed and result.daily_count == 400
    assert "daily_rate_limit" in result.reasons
    assert result.reset_after == int(day_window(clock.now)[1] - clock.now)
    clock.now = day_window(clock.now)[1]
    result = await service.check(RateLimitQo(123))
    assert result.allowed and result.daily_count == result.minute_count == 1


@pytest.mark.asyncio
async def test_parallel_requests_cannot_overdraw():
    """并发交付最多三十次获准，拒绝不推高日额度。"""
    service, _ = make_service()
    results = await asyncio.gather(
        *(service.check(RateLimitQo(123)) for _ in range(100))
    )
    assert sum(result.allowed for result in results) == 30
    snapshot = await service.check(RateLimitQo(123, charge=False))
    assert snapshot.minute_count == snapshot.daily_count == 30


@pytest.mark.asyncio
async def test_watch_renewal_read_only_observation_and_expiry():
    """观察查询不扣额度或续期，重复触发才延长观察。"""
    service, clock = make_service(max_requests=1)
    await service.check(RateLimitQo(123))
    await service.check(RateLimitQo(123))
    clock.now += 10
    denied = await service.check(RateLimitQo(123))
    assert not denied.allowed
    clock.now += 899
    observed = await service.check(RateLimitQo(123, charge=False))
    assert observed.watched and observed.reasons == ()
    assert observed.daily_count == 1
    clock.now += 1
    assert not (await service.check(RateLimitQo(123, charge=False))).watched


@pytest.mark.asyncio
async def test_expired_local_users_are_pruned():
    """过期用户状态会在后续检查中清理。"""
    service, clock = make_service()
    await service.check(RateLimitQo(123))
    clock.now = day_window(clock.now)[1] + 1
    await service.check(RateLimitQo(456))
    assert 123 not in service._states


@pytest.mark.asyncio
async def test_redis_failure_preserves_snapshot_and_recovery_supplies_local_state(
    caplog,
):
    """连接中断沿用已知计数，恢复检查携带本地累计和原到期时间。"""
    service, clock = make_service()
    day_end = day_window(clock.now)[1]
    minute_end = clock.now + 60
    client = AsyncMock()
    client.eval.side_effect = [
        [28, str(minute_end), 98, str(day_end), "0", 0, str(clock.now)],
        ConnectionError("sensitive-redis-url"),
        ConnectionError("sensitive-redis-url"),
        [
            30,
            str(minute_end),
            100,
            str(day_end),
            str(clock.now + 900),
            1,
            str(clock.now),
        ],
    ]
    service._redis = client
    caplog.set_level(logging.INFO)
    assert (await service.check(RateLimitQo(123))).mode == "redis"
    assert (await service.check(RateLimitQo(123))).daily_count == 99
    assert (await service.check(RateLimitQo(123))).daily_count == 100
    result = await service.check(RateLimitQo(123))
    assert result.mode == "redis" and not result.allowed
    args = client.eval.await_args.args
    assert args[10:15] == (30, minute_end, 100, day_end, 0)
    assert all(
        key.startswith("odysseia-protect:rate-limit:{123}:") for key in args[2:5]
    )
    transitions = [record.getMessage() for record in caplog.records]
    assert sum("切换为进程内" in message for message in transitions) == 1
    assert sum("已恢复" in message for message in transitions) == 1
    assert all("sensitive-redis-url" not in message for message in transitions)


@pytest.mark.asyncio
async def test_lifecycle_falls_back_and_closes_own_client():
    """启动失败保留重试客户端，关闭时释放连接。"""
    service, _ = make_service()
    client = AsyncMock()
    client.ping.side_effect = ConnectionError()
    service._redis = client
    await service.start()
    assert service._mode == "local"
    await service.close()
    client.aclose.assert_awaited_once()
    assert service._redis is None


@pytest.mark.parametrize(
    "name",
    [
        "RATE_LIMIT_MAX_REQUESTS",
        "RATE_LIMIT_WINDOW_SECONDS",
        "RATE_LIMIT_DAILY_WATCH_THRESHOLD",
        "RATE_LIMIT_DAILY_MAX_REQUESTS",
        "RATE_LIMIT_WATCH_SECONDS",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1", "bad", "1.5"])
def test_invalid_environment_config_fails(name, value):
    """非法数字配置明确失败，不静默放行。"""
    with pytest.raises(ValueError):
        RateLimitConfig.from_environment({name: value})


def test_watch_threshold_must_precede_daily_limit():
    """日观察阈值必须低于日拦截阈值。"""
    with pytest.raises(ValueError):
        RateLimitConfig.from_environment({"RATE_LIMIT_DAILY_WATCH_THRESHOLD": "400"})


def test_day_window_is_explicitly_beijing():
    """UTC 下午十六点对应北京时间自然日切换。"""
    now = datetime(2026, 10, 9, 23, 59, 59, tzinfo=BEIJING).timestamp()
    date, end = day_window(now)
    assert date == "20261009" and end - now == 1
    assert day_window(end)[0] == "20261010"
