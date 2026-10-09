from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    """可由表现层安全使用的限流检查结果。"""

    allowed: bool
    """本次交付是否获准，拒绝时不增加分钟和日累计额度。"""

    minute_count: int
    """当前固定窗口已获准的交付尝试次数，后续交付失败仍计入。"""

    daily_count: int
    """当前北京时间自然日已获准的交付尝试次数。"""

    minute_limit: int
    """当前固定窗口允许的交付次数上限。"""

    daily_limit: int
    """当前北京时间自然日允许的交付次数上限。"""

    reset_after: int
    """被拒绝后需要等待的秒数，同时命中多个限制时取最长等待时间。"""

    reasons: tuple[str, ...]
    """触发原因集合，可包含分钟限流、每日限流和每日观察。"""

    watched: bool
    """当前用户是否处于观察期，决定是否记录下载链路操作日志。"""

    mode: str
    """本次检查使用的后端，redis 表示共享状态，local 表示进程内降级。"""
