from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ..models import Resource, WishlistItem
from ..schemas import WishlistItemCreate, WishlistItemUpdate
from .base import BaseRepository


class WishlistRepository(
    BaseRepository[WishlistItem, WishlistItemCreate, WishlistItemUpdate]
):
    """心愿单项目的数据访问层。"""

    def __init__(self):
        super().__init__(model=WishlistItem)

    async def get_by_user_and_resource(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        resource_id: int,
    ) -> WishlistItem | None:
        statement = select(self.model).where(
            self.model.user_id == user_id,
            self.model.resource_id == resource_id,
        )
        result = await session.execute(statement)
        return result.scalar_one_or_none()

    async def is_wishlisted(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        resource_id: int,
    ) -> bool:
        return (
            await self.get_by_user_and_resource(
                session,
                user_id=user_id,
                resource_id=resource_id,
            )
            is not None
        )

    async def add_idempotent(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        resource_id: int,
    ) -> bool:
        statement = (
            insert(self.model)
            .values(user_id=user_id, resource_id=resource_id)
            .on_conflict_do_nothing(index_elements=["user_id", "resource_id"])
        )
        result = await session.execute(statement)
        return bool(result.rowcount)

    async def remove_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        resource_id: int,
    ) -> bool:
        item = await self.get_by_user_and_resource(
            session,
            user_id=user_id,
            resource_id=resource_id,
        )
        if item is None:
            return False
        await session.delete(item)
        return True

    async def count_for_user(self, session: AsyncSession, *, user_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.user_id == user_id)
        )
        result = await session.execute(statement)
        return int(result.scalar_one())

    async def get_page_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        offset: int,
        limit: int,
    ) -> Sequence[WishlistItem]:
        statement = (
            select(self.model)
            .where(self.model.user_id == user_id)
            .options(
                joinedload(self.model.resource).joinedload(Resource.thread)
            )
            .order_by(self.model.created_at.desc(), self.model.id.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(statement)
        return result.scalars().all()
