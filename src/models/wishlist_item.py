"""用户与具体资源版本的收藏关联模型。"""

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .resource import Resource
    from .user import User


class WishlistItem(Base):
    """保存用户收藏的具体资源版本，不按整个帖子合并收藏。"""

    __tablename__ = "wishlist_items"
    # 同一用户不能重复收藏同一资源版本；联合索引用于按用户和收藏时间查询。
    __table_args__ = (
        UniqueConstraint("user_id", "resource_id", name="uq_wishlist_user_resource"),
        Index("ix_wishlist_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    """收藏记录的数据库主键 ID。"""

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    """收藏者的 Discord 用户 ID，关联 users.id；用户删除时级联删除收藏。"""

    resource_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=False,
    )
    """被收藏资源版本的数据库 ID，关联 resources.id；资源删除时级联删除收藏。"""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    """加入心愿单的时间，用于收藏列表排序。"""

    user: Mapped["User"] = relationship("User", back_populates="wishlist_items")
    """发起收藏的用户记录。"""

    resource: Mapped["Resource"] = relationship(
        "Resource", back_populates="wishlist_items"
    )
    """被收藏的具体资源版本，下载时仍遵循该版本的密码和溯源设置。"""

    def __repr__(self) -> str:
        return (
            f"<WishlistItem(id={self.id}, user_id={self.user_id}, "
            f"resource_id={self.resource_id})>"
        )
