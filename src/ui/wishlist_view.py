import logging
from typing import TYPE_CHECKING

import discord

from src.database.database import AsyncSessionLocal
from src.exceptions.rate_limit_exceeded import RateLimitExceeded
from src.utils.download_guard import (
    observe_download,
    send_rate_limit_message,
    update_operation,
)

if TYPE_CHECKING:
    from src.services.wishlist_service import WishlistService

logger = logging.getLogger(__name__)


class WishlistView(discord.ui.LayoutView):
    """用户专属的心愿单分页视图。"""

    def __init__(
        self,
        *,
        service: "WishlistService",
        user_id: int,
        page: int,
        max_page: int,
    ):
        """保存交互所需的用户、页面和服务依赖。"""
        # 保存交互所需的用户、页面和服务依赖。
        super().__init__(timeout=14400)
        self.service = service
        self.user_id = user_id
        self.page = page
        self.max_page = max_page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """限制个人面板只能由所属用户操作。"""
        # 限制个人面板只能由所属用户操作。
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "❌ 这不是您的心愿单面板。",
            ephemeral=True,
        )
        return False

    @observe_download("wishlist_page")
    async def _go_to_page(self, interaction: discord.Interaction, page: int):
        """按额度刷新目标页并更新私密面板。"""
        # 按额度刷新目标页并更新私密面板。
        update_operation(page=page)
        await interaction.response.defer()
        try:
            async with AsyncSessionLocal() as session:
                render = await self.service.build_render(
                    session,
                    user_id=self.user_id,
                    page=page,
                )
        except RateLimitExceeded as error:
            await send_rate_limit_message(interaction, error)
            return
        attachments = [render.file] if render.file else []
        await interaction.edit_original_response(
            view=render.view,
            attachments=attachments,
        )

    @observe_download("wishlist_remove_refresh")
    async def _remove_and_refresh(
        self,
        interaction: discord.Interaction,
        *,
        resource_id: int | None = None,
        item_ids: tuple[int, ...] = (),
    ):
        """先提交删除，再按交付额度刷新心愿单。"""
        await interaction.response.defer()
        async with AsyncSessionLocal() as session:
            try:
                # 删除操作独立提交，刷新被限流不会撤销删除。
                if resource_id is not None:
                    await self.service.remove(
                        session,
                        user_id=self.user_id,
                        resource_id=resource_id,
                    )
                else:
                    await self.service.remove_items(
                        session,
                        user_id=self.user_id,
                        item_ids=item_ids,
                    )
                await session.commit()
            except Exception:
                await session.rollback()
                update_operation(outcome="failed")
                logger.exception("从心愿单移除项目失败")
                await interaction.followup.send(
                    "❌ 移除心愿单项目时发生内部错误，请稍后重试。",
                    ephemeral=True,
                )
                return

            try:
                # 刷新会重新取链，必须经过整页交付额度检查。
                render = await self.service.build_render(
                    session,
                    user_id=self.user_id,
                    page=self.page,
                )
            except RateLimitExceeded as error:
                update_operation(outcome="deleted_refresh_denied")
                await self._clear_after_remove(
                    interaction,
                    "✅ 删除成功，刷新受限。"
                    + error.message
                    + " 请稍后重新打开 `/心愿单`。",
                )
                return
            except Exception:
                update_operation(outcome="failed")
                logger.exception("心愿单删除已成功，但刷新失败")
                await self._clear_after_remove(
                    interaction,
                    "✅ 删除成功，但刷新失败，请稍后重新打开 `/心愿单`。",
                )
                return

        # 数据库会话关闭后更新独立 DTO 构建的视图。
        attachments = [render.file] if render.file else []
        await interaction.edit_original_response(
            view=render.view, attachments=attachments
        )
        self.stop()

    async def _clear_after_remove(self, interaction: discord.Interaction, message: str):
        """用纯提示组件替换旧链接，兼容 Discord Components V2。"""
        # V2 消息不能使用 content 字段，提示也通过组件发送。
        view = discord.ui.LayoutView()
        view.add_item(discord.ui.TextDisplay(message))
        await interaction.edit_original_response(view=view, attachments=[])
        view.stop()
        self.stop()

    async def _remove_resource(
        self,
        interaction: discord.Interaction,
        resource_id: int,
    ):
        """删除指定收藏项并刷新当前页面。"""
        # 删除指定收藏项并刷新当前页面。
        await self._remove_and_refresh(
            interaction,
            resource_id=resource_id,
        )

    async def _remove_page_items(
        self,
        interaction: discord.Interaction,
        item_ids: tuple[int, ...],
    ):
        """按页面快照删除收藏项并刷新。"""
        # 按页面快照删除收藏项并刷新。
        await self._remove_and_refresh(
            interaction,
            item_ids=item_ids,
        )

    async def on_timeout(self):
        # 私密交互令牌可能已经失效，不再尝试编辑消息。
        """停止过期面板而不编辑失效的交互消息。"""
        # 停止过期面板而不编辑失效的交互消息。
        pass
