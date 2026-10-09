from dataclasses import dataclass

from src.dto.wishlist_page_entry import WishlistPageEntry


@dataclass(frozen=True)
class WishlistPage:
    """可在数据库会话关闭后使用的整页心愿单交付数据。"""

    entries: list[WishlistPageEntry]
    """当前页的收藏项及交付结果，取链失败的项目仍保留错误说明。"""

    page: int
    """校正后的实际页码，从一开始。"""

    max_page: int
    """心愿单总页数，空列表时仍为一。"""

    total: int
    """用户当前收藏项目的总数。"""
