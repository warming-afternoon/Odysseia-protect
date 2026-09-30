"""管理员提交的动态溯源核验任务模型。"""

import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TraceVerificationJob(Base):
    """记录单个上传附件的核验任务、处理结果和报告保留期限。"""

    __tablename__ = "trace_verification_jobs"
    # 按提交人和创建时间检索任务；按过期时间检索待清理报告。
    __table_args__ = (
        Index("ix_trace_jobs_requester_created", "requester_id", "created_at"),
        Index("ix_trace_jobs_expires", "expires_at"),
    )

    report_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    """核验任务的报告编号，同时作为主键；一个附件对应一个独立报告。"""

    requester_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """提交核验任务的管理组成员 Discord 用户 ID。"""

    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """提交核验任务所在的 Discord 服务器 ID。"""

    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """提交核验任务所在的 Discord 频道 ID。"""

    input_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    """提交核验的原始附件文件名，可为 PNG、ZIP 或 7z。"""

    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued", nullable=False
    )
    """任务状态：queued 等待处理、processing 处理中、completed 已完成、failed 已失败。"""

    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    """完整内部核验结果的 JSON 文本，包含统计汇总和逐项明细；完成前可为空。"""

    report_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    """报告 ZIP 在私有 R2 中的对象键；未上传对象存储时可为空。"""

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    """任务失败的原因说明；没有失败信息时为空。"""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    """核验任务记录的创建时间。"""

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
    """最近更新时间；状态变更时写入不带时区的 UTC 时间。"""

    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    """报告保留期限的截止时间，使用不带时区的 UTC 时间，供定期清理任务查询。"""

