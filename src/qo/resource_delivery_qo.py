from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceDeliveryQo:
    """单资源交付的下载者身份。"""

    user_id: int
    """下载者的 Discord 用户 ID，跨帖子、跨服务器共享同一额度。"""
