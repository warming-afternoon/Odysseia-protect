from dataclasses import dataclass


@dataclass
class RateLimitStateDTO:
    """Redis 状态的进程内镜像及故障期间的额度状态。"""

    minute_count: int = 0
    """当前固定窗口已获准的交付尝试次数，后续交付失败仍计入。"""

    minute_end: float = 0
    """当前固定窗口到期的 Unix 时间戳，零表示尚未开始或已经到期。"""

    daily_count: int = 0
    """当前北京时间自然日已获准的交付尝试次数。"""

    daily_end: float = 0
    """当前日累计到期的 Unix 时间戳，对应北京时间次日零点。"""

    watch_end: float = 0
    """观察状态到期的 Unix 时间戳，跨日时仍可保留剩余观察时间。"""
