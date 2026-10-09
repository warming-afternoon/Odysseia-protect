from pydantic import BaseModel


class UserUpdate(BaseModel):
    """按已提供字段更新用户协议状态的参数。"""

    has_agreed_to_privacy_policy: bool | None = None
    """要更新的字段；未提供时不参与更新。用户是否已同意上传与资源存储协议，新用户默认为未同意。"""

    has_agreed_to_wishlist_policy: bool | None = None
    """要更新的字段；未提供时不参与更新。用户是否已同意心愿单存储协议，新用户默认为未同意。"""
