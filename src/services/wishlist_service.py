import asyncio
import logging
import math
from typing import Literal, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.repositories.resource import ResourceRepository
from src.database.repositories.thread import ThreadRepository
from src.database.repositories.user import UserRepository
from src.database.repositories.wishlist import WishlistRepository
from src.dto.resource_dto import ResourceDTO
from src.dto.wishlist_page import WishlistPage
from src.dto.wishlist_page_entry import WishlistPageEntry
from src.enums.wishlist import WISHLIST_PAGE_SIZE
from src.models import WishlistItem
from src.qo.user_create import UserCreate
from src.services.base import BaseService
from src.utils.download_guard import require_delivery, update_operation

logger = logging.getLogger(__name__)

WishlistMutationResult = Literal[
    "added",
    "already_added",
    "removed",
    "not_found",
    "needs_consent",
]


class WishlistService(BaseService):
    """心愿单的持久化、取链和分页服务。"""

    def __init__(
        self,
        bot,
        resource_repo: ResourceRepository,
        thread_repo: ThreadRepository,
        user_repo: UserRepository,
        wishlist_repo: WishlistRepository,
    ):
        """保存服务及仓储依赖。"""
        super().__init__(bot, resource_repo, thread_repo, user_repo)
        self.wishlist_repo = wishlist_repo

    async def is_wishlisted(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        resource_id: int,
    ) -> bool:
        """查询当前资源是否属于用户心愿单。"""
        return await self.wishlist_repo.is_wishlisted(
            session,
            user_id=user_id,
            resource_id=resource_id,
        )

    async def add(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        resource_id: int,
    ) -> WishlistMutationResult:
        """检查协议与资源后幂等添加心愿单项目。"""
        if await self.resource_repo.get(session, id=resource_id) is None:
            return "not_found"

        user = await self.user_repo.get(session, id=user_id)
        if user is None or not user.has_agreed_to_wishlist_policy:
            return "needs_consent"

        created = await self.wishlist_repo.add_idempotent(
            session,
            user_id=user_id,
            resource_id=resource_id,
        )
        return "added" if created else "already_added"

    async def accept_policy_and_add(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        resource_id: int,
    ) -> WishlistMutationResult:
        """保存协议同意状态并添加心愿单项目。"""
        if await self.resource_repo.get(session, id=resource_id) is None:
            return "not_found"

        user = await self.user_repo.get(session, id=user_id)
        if user is None:
            user = await self.user_repo.create(
                session,
                obj_in=UserCreate(
                    id=user_id,
                    has_agreed_to_privacy_policy=False,
                    has_agreed_to_wishlist_policy=True,
                ),
            )
            await session.flush()
        else:
            await self.user_repo.update(
                session,
                db_obj=user,
                obj_in={"has_agreed_to_wishlist_policy": True},
            )

        created = await self.wishlist_repo.add_idempotent(
            session,
            user_id=user_id,
            resource_id=resource_id,
        )
        return "added" if created else "already_added"

    async def remove(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        resource_id: int,
    ) -> WishlistMutationResult:
        """移除用户的单个收藏资源。"""
        removed = await self.wishlist_repo.remove_for_user(
            session,
            user_id=user_id,
            resource_id=resource_id,
        )
        return "removed" if removed else "not_found"

    async def remove_items(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        item_ids: Sequence[int],
    ) -> int:
        """按页面快照批量移除用户收藏项目。"""
        return await self.wishlist_repo.remove_items_for_user(
            session,
            user_id=user_id,
            item_ids=item_ids,
        )

    @staticmethod
    def _to_resource_dto(item: WishlistItem) -> ResourceDTO:
        """将已预加载关联的收藏资源转换为独立 DTO。"""
        resource = item.resource
        return ResourceDTO(
            id=resource.id,
            filename=resource.filename,
            version_info=resource.version_info,
            password=resource.password,
            source_message_id=resource.source_message_id,
            warehouse_thread_id=resource.thread.warehouse_thread_id,
            public_thread_id=resource.thread.public_thread_id,
            author_id=resource.thread.author_id,
            guild_id=resource.thread.guild_id,
            public_thread_name=resource.thread.public_thread_name,
            source_status=resource.thread.source_status,
            upload_mode=resource.upload_mode,
            trace_enabled=resource.trace_enabled,
        )

    async def get_page(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        page: int,
    ) -> WishlistPage:
        """按用户额度取得整页资源交付数据。"""
        total = await self.wishlist_repo.count_for_user(session, user_id=user_id)
        max_page = max(1, math.ceil(total / WISHLIST_PAGE_SIZE))
        safe_page = min(max(1, page), max_page)
        items = await self.wishlist_repo.get_page_for_user(
            session,
            user_id=user_id,
            offset=(safe_page - 1) * WISHLIST_PAGE_SIZE,
            limit=WISHLIST_PAGE_SIZE,
        )

        resource_dtos = [self._to_resource_dto(item) for item in items]
        download_service = getattr(self.bot, "download_service", None)
        if download_service is None:
            raise RuntimeError("Bot 未配置下载服务。")

        # 关联数据已由仓储预加载，整页取链前只申请一次额度。
        update_operation(page=safe_page)
        if resource_dtos:
            await require_delivery(self.bot, user_id)

        async def fetch_url(resource: ResourceDTO) -> str:
            """调用底层交付方法，不重复扣整页额度。"""
            if not resource.trace_enabled:
                return await download_service.fetch_fresh_url(resource)
            delivery = await download_service.fetch_delivery(resource, user_id=user_id)
            if not delivery.url:
                raise RuntimeError("R2 暂不可用，请从下载面板获取私密附件")
            return delivery.url

        results = await asyncio.gather(
            *(fetch_url(resource) for resource in resource_dtos),
            return_exceptions=True,
        )

        entries: list[WishlistPageEntry] = []
        for item, resource, result in zip(items, resource_dtos, results):
            if isinstance(result, BaseException):
                update_operation(outcome="partial_failure")
                logger.warning(
                    "心愿单资源 %s 取链失败: %s",
                    resource.id,
                    result,
                )
                entries.append(
                    WishlistPageEntry(
                        item_id=item.id,
                        resource=resource,
                        created_at=item.created_at,
                        url=None,
                        error="源消息或附件已不可用",
                    )
                )
            else:
                entries.append(
                    WishlistPageEntry(
                        item_id=item.id,
                        resource=resource,
                        created_at=item.created_at,
                        url=result,
                    )
                )

        return WishlistPage(
            entries=entries,
            page=safe_page,
            max_page=max_page,
            total=total,
        )

    async def build_render(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        page: int,
    ):
        """通过独立分页 DTO 构建心愿单展示结果。"""
        from src.ui.wishlist_ui import build_wishlist_render

        page_data = await self.get_page(
            session,
            user_id=user_id,
            page=page,
        )
        return build_wishlist_render(
            service=self,
            user_id=user_id,
            page_data=page_data,
        )
