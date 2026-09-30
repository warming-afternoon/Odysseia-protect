"""帖内普通资源和受保护资源的模型。"""

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.enums import UploadMode
from .base import Base

if TYPE_CHECKING:
    from .thread import Thread
    from .wishlist_item import WishlistItem


class Resource(Base):
    """保存一个具体资源版本的文件位置、下载设置和溯源开关。"""

    __tablename__ = "resources"

    # --- 表字段 ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    """资源版本的数据库主键 ID，供下载、管理和心愿单引用。"""

    thread_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    """所属帖子记录的数据库 ID，关联 threads.id；帖子记录删除时级联删除资源。"""

    version_info: Mapped[str] = mapped_column(Text, nullable=False)
    """作者或协作者填写的版本说明，用于下载列表和资源管理面板。"""

    upload_mode: Mapped[UploadMode] = mapped_column(Enum(UploadMode), nullable=False)
    """资源模式：NORMAL 引用公开帖内消息，SECURE 将附件保存在私密仓库。"""

    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    """受保护资源的下载密码；为空时不要求输入密码。"""

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    """可选的资源描述或备注；未填写时为空。"""

    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """源消息的 Discord ID；普通资源指向公开消息，受保护资源指向仓库消息。"""

    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """资源的显示文件名；普通资源也可由源消息内容或版本说明生成。"""

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())
    """资源版本登记到 Bot 的时间。"""

    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    """Bot 记录的成功下载次数；默认从 0 开始计数。"""

    trace_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    """是否启用动态溯源；启用时为下载者生成带溯源凭证的 PNG 角色卡。"""

    # --- 关系 ---
    # 多个 Resource 属于一个 Thread
    thread: Mapped["Thread"] = relationship("Thread", back_populates="resources")
    """资源所属的帖子记录，提供原作者、公开来源和私密仓库信息。"""

    wishlist_items: Mapped[list["WishlistItem"]] = relationship(
        "WishlistItem", back_populates="resource", cascade="all, delete-orphan"
    )
    """收藏此资源版本的心愿单记录；资源删除时同步删除对应收藏。"""

    def __repr__(self) -> str:
        return f"<Resource(id={self.id}, version='{self.version_info}', filename='{self.filename}')>"
