# -*- coding: utf-8 -*-
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.enums import SourceStatus

from ..models import Thread
from ..schemas import ThreadCreate, ThreadUpdate
from .base import BaseRepository


class ThreadRepository(BaseRepository[Thread, ThreadCreate, ThreadUpdate]):
    def __init__(self):
        super().__init__(model=Thread)

    async def get_by_public_thread_id(
        self, session: AsyncSession, *, public_thread_id: int
    ) -> Thread | None:
        """
        根据公开的 Discord 帖子 ID 获取数据库记录。

        这是一个特定于 ThreadRepository 的查询方法。

        :param session: 数据库会话。
        :param public_thread_id: Discord 帖子的唯一 ID。
        :return: 找到的 Thread 对象，如果不存在则返回 None。
        """
        statement = select(self.model).where(
            self.model.public_thread_id == public_thread_id
        )
        result = await session.execute(statement)
        return result.scalar_one_or_none()

    async def get_missing_source_metadata(
        self,
        session: AsyncSession,
    ) -> list[Thread]:
        """获取尚未完成来源标题或服务器 ID 回填的帖子。"""
        statement = (
            select(self.model)
            .where(
                or_(
                    self.model.guild_id.is_(None),
                    self.model.public_thread_name.is_(None),
                )
            )
            .order_by(self.model.id)
        )
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def update_source_metadata(
        self,
        session: AsyncSession,
        *,
        public_thread_id: int,
        guild_id: int | None = None,
        public_thread_name: str | None = None,
        source_status: SourceStatus | None = None,
    ) -> Thread | None:
        """按 Discord 公开帖子 ID 更新中央来源元数据。"""
        thread = await self.get_by_public_thread_id(
            session,
            public_thread_id=public_thread_id,
        )
        if thread is None:
            return None

        if guild_id is not None:
            thread.guild_id = guild_id
        if public_thread_name is not None:
            thread.public_thread_name = public_thread_name[:100]
        if source_status is not None:
            thread.source_status = source_status
        session.add(thread)
        return thread
