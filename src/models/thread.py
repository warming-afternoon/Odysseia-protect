"""公开帖子与私密仓库的关联模型。"""

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.enums import SourceStatus
from .base import Base

if TYPE_CHECKING:
    from .resource import Resource


class Thread(Base):
    """保存帖子来源、原作者、仓库关联和资源维护设置。"""

    __tablename__ = "threads"

    # --- 表字段 ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    """帖子记录的数据库主键 ID，供资源外键引用。"""

    public_thread_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    """公开来源帖子的 Discord 频道 ID，全局唯一；旧文本频道记录也使用此字段。"""

    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    """公开来源所在的 Discord 服务器 ID；旧数据尚未回填时可为空。"""

    public_thread_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    """公开来源的标题快照；尚未获取或回填时可为空。"""

    source_status: Mapped[SourceStatus] = mapped_column(
        Enum(
            SourceStatus,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
            native_enum=False,
        ),
        default=SourceStatus.UNKNOWN,
        server_default=SourceStatus.UNKNOWN.value,
        nullable=False,
    )
    """来源状态：unknown 表示尚未确认，active 表示来源可用，deleted 表示已删除。"""

    warehouse_thread_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True
    )
    """对应私密仓库帖子的 Discord ID；尚未创建受保护资源仓库时为空。"""

    download_panel_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    """历史固定下载面板的 Discord 消息 ID；未登记面板时为空。"""

    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """原帖主的 Discord 用户 ID；协作者上传时仍保存原帖主，不保存操作人。"""

    # reaction_required: Mapped[bool] = mapped_column(default=False, nullable=False)
    # reaction_emoji: Mapped[str | None] = mapped_column(String(50), nullable=True)
    quick_mode_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    """是否开启快捷模式；开启后，右键转存受保护资源成功时自动删除源消息。"""

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())
    """帖子关联记录的创建时间，不是 Discord 帖子的原始创建时间。"""

    # --- 关系 ---
    # 一个 Thread 可以有多个 Resource
    resources: Mapped[list["Resource"]] = relationship(
        "Resource", back_populates="thread", cascade="all, delete-orphan"
    )
    """本帖登记的全部资源；删除帖子记录时，ORM 同时删除资源及孤立关联。"""

    def __repr__(self) -> str:
        return f"<Thread(id={self.id}, public_thread_id={self.public_thread_id})>"
