from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitQo:
    """用户共享额度检查参数。"""

    user_id: int
    """下载者的 Discord 用户 ID，跨帖子、跨服务器共享同一额度。"""

    charge: bool = True
    """是否申请一次交付额度，False 仅查询计数与观察状态。"""
