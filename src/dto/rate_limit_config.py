import os
from dataclasses import dataclass
from typing import Mapping

from src.utils.env_config import positive_int


@dataclass(frozen=True)
class RateLimitConfig:
    """共享资源交付额度和观察阈值配置。"""

    max_requests: int = 30
    """单个固定窗口内允许的资源交付次数，单资源和心愿单一页均计一次。"""

    window_seconds: int = 60
    """分钟额度窗口的秒数，从首次获准交付开始计时。"""

    daily_watch_threshold: int = 100
    """北京时间自然日交付次数超过此值时触发观察，不直接拒绝交付。"""

    daily_max_requests: int = 400
    """北京时间自然日允许的最大交付次数，达到后拒绝后续交付。"""

    watch_seconds: int = 900
    """观察状态保留的秒数，再次触发时续期。"""

    def __post_init__(self):
        """校验限流阈值和窗口配置。"""
        # 确保所有阈值为正整数，观察阈值早于日拦截阈值。
        if any(
            type(value) is not int or value <= 0
            for value in (
                self.max_requests,
                self.window_seconds,
                self.daily_watch_threshold,
                self.daily_max_requests,
                self.watch_seconds,
            )
        ):
            raise ValueError("限流配置必须是正整数")
        if self.daily_watch_threshold >= self.daily_max_requests:
            raise ValueError(
                "RATE_LIMIT_DAILY_WATCH_THRESHOLD 必须小于 RATE_LIMIT_DAILY_MAX_REQUESTS"
            )

    @classmethod
    def from_environment(cls, values: Mapping[str, str] | None = None):
        """读取并校验环境变量中的限流配置。"""
        # 配置集中解析，防止非法值意外关闭保护。
        values = os.environ if values is None else values
        return cls(
            max_requests=positive_int(values, "RATE_LIMIT_MAX_REQUESTS", 30),
            window_seconds=positive_int(values, "RATE_LIMIT_WINDOW_SECONDS", 60),
            daily_watch_threshold=positive_int(
                values, "RATE_LIMIT_DAILY_WATCH_THRESHOLD", 100
            ),
            daily_max_requests=positive_int(
                values, "RATE_LIMIT_DAILY_MAX_REQUESTS", 400
            ),
            watch_seconds=positive_int(values, "RATE_LIMIT_WATCH_SECONDS", 900),
        )
