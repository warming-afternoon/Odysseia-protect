"""Discord 附件签名链接的缓存期限计算。"""

import math
from urllib.parse import parse_qs, urlsplit


class SourceUrlCacheTtl:
    """根据附件签名有效期限制源链接的缓存时间。"""

    CACHE_TTL_SECONDS = 6 * 3600
    """源附件链接的默认缓存期限及上限，单位为秒。"""

    EXPIRY_MARGIN_SECONDS = 3600
    """签名剩余有效期不足默认缓存期限时预留的时间，单位为秒。"""

    @staticmethod
    def cache_ttl(url: str, now: float) -> int:
        """根据附件签名到期时间计算缓存期限。"""
        # 缓存最多保留六小时；签名剩余不足六小时时预留一小时。
        try:
            expiry = int(parse_qs(urlsplit(url).query)["ex"][0], 16)
            remaining = expiry - now
            ttl = (
                SourceUrlCacheTtl.CACHE_TTL_SECONDS
                if remaining >= SourceUrlCacheTtl.CACHE_TTL_SECONDS
                else remaining - SourceUrlCacheTtl.EXPIRY_MARGIN_SECONDS
            )
            return max(0, math.floor(ttl))
        except (KeyError, ValueError, IndexError, OverflowError):
            return 0
