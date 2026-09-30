"""原作者与协作者的跨服授权模型。"""

import datetime

from sqlalchemy import BigInteger, DateTime, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AuthorCollaborator(Base):
    """保存原作者授予其他账号的资源维护权限，不依赖用户或帖子记录。"""

    __tablename__ = "author_collaborators"
    # 联合唯一约束：同一原作者与协作者只能保留一条有效授权。
    __table_args__ = (
        UniqueConstraint("author_id", "collaborator_id", name="uq_author_collaborator"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    """授权记录的数据库主键 ID。"""

    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """原作者的 Discord 用户 ID；授权覆盖其所有服务器的现有及未来帖子。"""

    collaborator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """获得资源维护权限的 Discord 用户 ID，可同时协作多个原作者。"""

    granted_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """创建这条授权的操作人 Discord 用户 ID，为原作者本人或管理组成员。"""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), server_default=func.now()
    )
    """授权创建时间；直接写入数据库时也由数据库生成默认时间。"""

