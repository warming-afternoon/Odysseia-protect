"""基于 Redis 原子检查与本地降级的共享下载额度。"""

import asyncio
import logging
import math
import os
import time
from typing import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.dto.rate_limit_config import RateLimitConfig
from src.dto.rate_limit_result import RateLimitResult
from src.dto.rate_limit_state_dto import RateLimitStateDTO
from src.qo.rate_limit_qo import RateLimitQo
from src.utils.beijing_time import day_window

logger = logging.getLogger(__name__)


# 额度检查与本地状态合并在同一次原子调用中完成。
# 绝对到期时间确保 Redis 中断后仍沿用原来的固定窗口。
_RATE_LIMIT_SCRIPT = """
local stamp = redis.call('TIME')
local now = tonumber(stamp[1]) + tonumber(stamp[2]) / 1000000
local beijing_midnight = (math.floor((now + 28800) / 86400) + 1) * 86400 - 28800
local function read_counter(key, local_count, local_end)
    local count = tonumber(redis.call('HGET', key, 'count') or '0')
    local ending = tonumber(redis.call('HGET', key, 'ending') or '0')
    if ending <= now then count = 0; ending = 0 end
    if local_end > now then
        count = math.max(count, local_count)
        ending = math.max(ending, local_end)
    end
    return count, ending
end
local minute, minute_end = read_counter(KEYS[1], tonumber(ARGV[6]), tonumber(ARGV[7]))
local daily, daily_end = read_counter(KEYS[2], tonumber(ARGV[8]), tonumber(ARGV[9]))
local watch_end = math.max(tonumber(redis.call('GET', KEYS[3]) or '0'), tonumber(ARGV[10]))
local charge = tonumber(ARGV[5]) == 1
local reason = 0
if charge then
    if minute >= tonumber(ARGV[1]) then reason = reason + 1 end
    if daily >= tonumber(ARGV[2]) then reason = reason + 2 end
    if reason == 0 then
        if minute == 0 then minute_end = now + tonumber(ARGV[3]) end
        if daily == 0 then daily_end = beijing_midnight end
        minute = minute + 1
        daily = daily + 1
    end
    if reason ~= 0 or daily > tonumber(ARGV[4]) then
        watch_end = math.max(watch_end, now + tonumber(ARGV[11]))
    end
end
local function save_counter(key, count, ending)
    if count > 0 and ending > now then
        redis.call('HSET', key, 'count', count, 'ending', tostring(ending))
        redis.call('EXPIREAT', key, math.ceil(ending))
    end
end
save_counter(KEYS[1], minute, minute_end)
save_counter(KEYS[2], daily, daily_end)
if watch_end > now then
    redis.call('SET', KEYS[3], tostring(watch_end))
    redis.call('EXPIREAT', KEYS[3], math.ceil(watch_end))
end
return {minute, tostring(minute_end), daily, tostring(daily_end), tostring(watch_end), reason, tostring(now)}
"""


class RateLimitService:
    """协调共享限流与故障降级的服务。"""

    def __init__(
        self,
        config: RateLimitConfig | None = None,
        *,
        redis_url: str | None = None,
        clock: Callable[[], float] = time.time,
    ):
        """初始化配置和运行状态。"""
        # 保存配置与生命周期状态。
        self.config = (
            config if config is not None else RateLimitConfig.from_environment()
        )
        self.redis_url = os.getenv("REDIS_URL") if redis_url is None else redis_url
        self._clock = clock
        self._redis: Redis | None = None
        self._states: dict[int, RateLimitStateDTO] = {}
        self._lock = asyncio.Lock()
        self._mode: str | None = None
        self._next_prune = 0.0

    def _set_mode(self, mode: str):
        """仅在后端状态变化时记录降级或恢复。"""
        # 相同模式不重复打印故障日志。
        if self._mode == mode:
            return
        previous = self._mode
        self._mode = mode
        if mode == "local":
            logger.warning(
                "下载限流切换为进程内模式；重启会丢失本地额度，多实例无法共享。"
            )
        else:
            logger.info(
                "下载限流 Redis %s。",
                "已恢复，合并本地计数" if previous == "local" else "已连接",
            )

    async def start(self):
        """建立 Redis 连接，失败时启用本地限流。"""
        # 限制连接超时，避免故障时长期阻塞 Discord 交互。
        if self.redis_url and self._redis is None:
            self._redis = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        if self._redis is not None:
            try:
                await self._redis.ping()
                self._set_mode("redis")
                return
            except (RedisError, OSError, asyncio.TimeoutError):
                pass
        self._set_mode("local")

    async def close(self):
        """关闭 Redis 连接并释放进程内状态。"""
        # 关闭自有连接，避免影响动态交付服务的 Redis 客户端。
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        self._states.clear()

    def _state(self, user_id: int, now: float) -> RateLimitStateDTO:
        """取得用户当前状态并清理过期计数。"""
        # 定期移除全部到期的用户状态，避免内存随访问用户增长。
        if now >= self._next_prune:
            self._states = {
                uid: state
                for uid, state in self._states.items()
                if max(state.minute_end, state.daily_end, state.watch_end) > now
            }
            self._next_prune = now + 60
        state = self._states.setdefault(user_id, RateLimitStateDTO())
        if state.minute_end <= now:
            state.minute_count = 0
            state.minute_end = 0
        if state.daily_end <= now:
            state.daily_count = 0
            state.daily_end = 0
        return state

    def _result(
        self, state: RateLimitStateDTO, now: float, charge: bool, reason: int, mode: str
    ):
        """将内部状态转换为可供表现层使用的结果。"""
        # 汇总拒绝原因，同时受限时取最长等待时间。
        reasons = []
        waits = []
        if reason & 1:
            reasons.append("minute_rate_limit")
            waits.append(state.minute_end - now)
        if reason & 2:
            reasons.append("daily_rate_limit")
            waits.append(state.daily_end - now)
        if charge and state.daily_count > self.config.daily_watch_threshold:
            reasons.append("daily_watch")
        return RateLimitResult(
            allowed=reason == 0,
            minute_count=state.minute_count,
            daily_count=state.daily_count,
            minute_limit=self.config.max_requests,
            daily_limit=self.config.daily_max_requests,
            reset_after=max(1, math.ceil(max(waits))) if waits else 0,
            reasons=tuple(reasons),
            watched=state.watch_end > now,
            mode=mode,
        )

    async def check(self, qo: RateLimitQo) -> RateLimitResult:
        """原子检查共享额度并在获准后计数。"""
        # 同一进程串行合并状态，跨进程额度由 Redis 脚本保证原子性。
        user_id, charge = qo.user_id, qo.charge
        async with self._lock:
            now = self._clock()
            _, day_end = day_window(now)
            state = self._state(user_id, now)
            if self._redis is not None:
                # 同一用户的 Redis 键位于同一槽，兼容原子脚本检查。
                prefix = f"odysseia-protect:rate-limit:{{{user_id}}}"
                try:
                    raw = await self._redis.eval(
                        _RATE_LIMIT_SCRIPT,
                        3,
                        f"{prefix}:minute",
                        f"{prefix}:daily",
                        f"{prefix}:watch",
                        self.config.max_requests,
                        self.config.daily_max_requests,
                        self.config.window_seconds,
                        self.config.daily_watch_threshold,
                        int(charge),
                        state.minute_count,
                        state.minute_end,
                        state.daily_count,
                        state.daily_end,
                        state.watch_end,
                        self.config.watch_seconds,
                    )
                    state.minute_count, state.minute_end = int(raw[0]), float(raw[1])
                    state.daily_count, state.daily_end = int(raw[2]), float(raw[3])
                    state.watch_end = float(raw[4])
                    self._set_mode("redis")
                    return self._result(
                        state, float(raw[6]), charge, int(raw[5]), "redis"
                    )
                except (RedisError, OSError, asyncio.TimeoutError):
                    pass
            self._set_mode("local")
            reason = 0
            if charge:
                if state.minute_count >= self.config.max_requests:
                    reason |= 1
                if state.daily_count >= self.config.daily_max_requests:
                    reason |= 2
                if not reason:
                    if state.minute_count == 0:
                        state.minute_end = now + self.config.window_seconds
                    if state.daily_count == 0:
                        state.daily_end = day_end
                    state.minute_count += 1
                    state.daily_count += 1
                if reason or state.daily_count > self.config.daily_watch_threshold:
                    state.watch_end = max(
                        state.watch_end, now + self.config.watch_seconds
                    )
            return self._result(state, now, charge, reason, "local")


def rate_limiter_for(bot) -> RateLimitService:
    """为独立运行的服务提供真实本地限流器。"""
    # 独立运行的服务也使用真实计数，不能因缺少 Bot 初始化而放行。
    limiter = getattr(bot, "rate_limit_service", None)
    if not isinstance(limiter, RateLimitService):
        limiter = RateLimitService(redis_url="")
        bot.rate_limit_service = limiter
    return limiter
