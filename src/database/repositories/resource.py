from typing import Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import joinedload

from ..models import Resource
from ..schemas import ResourceCreate, ResourceUpdate
from .base import BaseRepository


class ResourceRepository(BaseRepository[Resource, ResourceCreate, ResourceUpdate]):
    """
    Resource 模型的数据库操作仓库。
    """

    def __init__(self):
        super().__init__(model=Resource)

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
