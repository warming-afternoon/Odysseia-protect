from pydantic import BaseModel


class WishlistItemBase(BaseModel):
    """用户与收藏资源关联的共用字段。"""

    user_id: int
    """心愿单归属用户的 Discord ID，对应用户记录主键。"""

    resource_id: int
    """心愿单收藏资源的数据库主键，对应资源记录。"""
