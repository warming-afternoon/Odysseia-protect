from pydantic import BaseModel


class UserBase(BaseModel):
    """用户身份及协议同意状态的基础数据。"""

    id: int
    """用户的 Discord ID，同时作为用户记录主键。"""

    has_agreed_to_privacy_policy: bool = False
    """用户是否已同意上传与资源存储协议，新用户默认为未同意。"""

    has_agreed_to_wishlist_policy: bool = False
    """用户是否已同意心愿单存储协议，新用户默认为未同意。"""
