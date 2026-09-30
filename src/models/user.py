"""Discord 用户与协议同意状态的模型。"""

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .wishlist_item import WishlistItem


class User(Base):
    """保存用户自己的协议同意状态及心愿单关联。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, index=True, autoincrement=False
    )
    """Discord 用户 ID，同时作为数据库主键；使用真实用户 ID，不自动递增。"""

    has_agreed_to_privacy_policy: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )
    """是否已同意资源上传与数据存储协议；协作者需独立同意，不能沿用原作者记录。"""

    has_agreed_to_wishlist_policy: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )
    """是否已同意心愿单数据存储协议，与上传协议的同意状态独立。"""

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())
    """该用户首次创建 Bot 用户记录的时间。"""

    wishlist_items: Mapped[list["WishlistItem"]] = relationship(
        "WishlistItem", back_populates="user", cascade="all, delete-orphan"
    )
    """该用户收藏的资源版本；删除用户记录时级联删除其收藏。"""

    def __repr__(self) -> str:
        return f"<User(id={self.id}, has_agreed={self.has_agreed_to_privacy_policy})>"
