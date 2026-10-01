from typing import Sequence
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import joinedload

from src.models import Resource
from ..schemas import ResourceCreate, ResourceUpdate
from .base import BaseRepository


class ResourceRepository(BaseRepository[Resource, ResourceCreate, ResourceUpdate]):
    """
    Resource 模型的数据库操作仓库。
    """

    def __init__(self):
        super().__init__(model=Resource)

    async def count_by_thread_id(self, session: AsyncSession, *, thread_id: int) -> int:
        """统计指定帖子中的资源版本总数。"""
        # 仅统计资源表，避免加载完整资源列表。
        statement = select(func.count()).select_from(self.model).where(
            self.model.thread_id == thread_id
        )
        return int(await session.scalar(statement) or 0)

    async def get_page_by_thread_id(
        self, session: AsyncSession, *, thread_id: int, offset: int, limit: int
    ) -> Sequence[Resource]:
        """按上传时间及主键倒序读取指定帖子的资源页。"""
        # 在数据库中排序和分页，同一上传时间由主键保证稳定顺序。
        statement = (
            select(self.model)
            .where(self.model.thread_id == thread_id)
            .order_by(self.model.created_at.desc(), self.model.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return (await session.execute(statement)).scalars().all()

    async def increment_download_count(self, session: AsyncSession, *, resource_id: int) -> bool:
        """原子增加一个资源的下载次数并返回是否存在。"""
        # 使用数据库表达式更新，避免并发下载丢失计数。
        result = await session.execute(
            update(self.model)
            .where(self.model.id == resource_id)
            .values(download_count=self.model.download_count + 1)
        )
        return bool(result.rowcount)

    async def get_by_thread_id(
        self, session: AsyncSession, thread_id: int
    ) -> Sequence[Resource]:
        """
        根据 thread_id 获取一个帖子的所有资源。

        :param session: 数据库会话。
        :param thread_id: 关联的 Thread 的 ID。
        :return: Resource 对象列表。
        """
        statement = select(self.model).where(self.model.thread_id == thread_id)
        result = await session.execute(statement)
        return result.scalars().all()

    async def get_with_thread(
        self, session: AsyncSession, *, id: int
    ) -> Resource | None:
        """
        通过 ID 获取一个资源，并立即加载其关联的 Thread 对象。
        这可以防止在后续访问 `resource.thread` 时产生额外的数据库查询。
        """
        statement = (
            select(self.model)
            .where(self.model.id == id)
            .options(joinedload(self.model.thread))
        )
        result = await session.execute(statement)
        return result.scalars().first()

    async def get_multi_by_thread_id(
        self, session: AsyncSession, *, thread_id: int
    ) -> Sequence[Resource]:
        """根据 thread_id 获取所有资源。"""
        return await self.get_multi(session, thread_id=thread_id)
