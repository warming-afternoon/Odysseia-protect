"""源附件链接缓存记录。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceUrlCacheEntry:
    """保存源附件链接及其绝对缓存到期时间。"""

    url: str
    """源消息附件的完整访问链接，包含 Discord CDN 签名参数。"""

    cache_until: float
    """缓存到期的 Unix 时间戳，单位为秒；命中缓存时不延长。"""
