from pydantic import ConfigDict

from .wishlist_item_base import WishlistItemBase


class WishlistItemInDB(WishlistItemBase):
    """可从心愿单关联 ORM 属性构建的独立数据。"""

    id: int
    """该记录的数据库主键 ID。"""

    model_config = ConfigDict(from_attributes=True)
    """允许读取 ORM 对象的属性，转换后可脱离数据库会话使用。"""
