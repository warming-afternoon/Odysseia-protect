# -*- coding: utf-8 -*-
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
