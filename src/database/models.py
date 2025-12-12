import datetime
import enum
from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class UploadMode(enum.Enum):
    SECURE = "secure"
    NORMAL = "normal"


class Thread(Base):
    """
    帖子关联模型 (Threads Table)
    """

    __tablename__ = "threads"

    # --- 表字段 ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_thread_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    warehouse_thread_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True
    )
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # reaction_required: Mapped[bool] = mapped_column(default=False, nullable=False)
    # reaction_emoji: Mapped[str | None] = mapped_column(String(50), nullable=True)
    quick_mode_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())

    # --- 关系 ---
    # 一个 Thread 可以有多个 Resource
    resources: Mapped[list["Resource"]] = relationship(
        "Resource", back_populates="thread", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Thread(id={self.id}, public_thread_id={self.public_thread_id})>"


class Resource(Base):
    """
    资源信息模型 (Resources Table)
    """

    __tablename__ = "resources"

    # --- 表字段 ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    version_info: Mapped[str] = mapped_column(Text, nullable=False)
    upload_mode: Mapped[UploadMode] = mapped_column(Enum(UploadMode), nullable=False)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())
    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- 关系 ---
    # 多个 Resource 属于一个 Thread
    thread: Mapped["Thread"] = relationship("Thread", back_populates="resources")

    def __repr__(self) -> str:
        return f"<Resource(id={self.id}, version='{self.version_info}', filename='{self.filename}')>"


class User(Base):
    """
    用户信息模型 (Users Table)
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, index=True, autoincrement=False
    )  # Discord User ID
    has_agreed_to_privacy_policy: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self) -> str:
        return f"<User(id={self.id}, has_agreed={self.has_agreed_to_privacy_policy})>"
