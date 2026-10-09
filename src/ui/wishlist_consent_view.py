import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from src.database.database import AsyncSessionLocal
from src.enums.wishlist import WISHLIST_ADDED_MESSAGE

if TYPE_CHECKING:
    from src.services.wishlist_service import WishlistService

logger = logging.getLogger(__name__)


class WishlistConsentView(discord.ui.View):
    """首次加入心愿单前的协议确认。"""

    def __init__(
        self,
        *,
        service: "WishlistService",
        user_id: int,
        resource_id: int,
        panel_view,
        panel_message: discord.Message | None,
    ):
        """保存交互所需的用户、页面和服务依赖。"""
        # 保存交互所需的用户、页面和服务依赖。
        super().__init__(timeout=300)
        self.service = service
        self.user_id = user_id
        self.resource_id = resource_id
        self.panel_view = panel_view
        self.panel_message = panel_message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """限制个人面板只能由所属用户操作。"""
        # 限制个人面板只能由所属用户操作。
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "❌ 这不是您的协议确认面板。",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="同意并加入", style=discord.ButtonStyle.success)
    async def agree(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        """同意存储协议并加入当前资源。"""
        # 同意存储协议并加入当前资源。
        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            try:
                result = await self.service.accept_policy_and_add(
                    session,
                    user_id=self.user_id,
                    resource_id=self.resource_id,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("同意协议并加入心愿单失败")
                await interaction.edit_original_response(
                    content="❌ 加入心愿单时发生内部错误。",
                    embed=None,
                    view=None,
                )
                return

        if result == "not_found":
            await interaction.edit_original_response(
                content="❌ 资源已不存在，无法加入心愿单。",
                embed=None,
                view=None,
            )
            return

        # 与下载面板翻页串行刷新，避免覆盖其他页的收藏状态。
        async with getattr(self.panel_view, "state_lock", asyncio.Lock()):
            selection_is_still_current = (
                getattr(self.panel_view, "selected_resource_id", None)
                == self.resource_id
            )
            if selection_is_still_current:
                self.panel_view.set_wishlist_state(True)
            if selection_is_still_current and self.panel_message is not None:
                try:
                    await self.panel_message.edit(view=self.panel_view)
                except discord.HTTPException:
                    logger.warning("无法刷新原下载面板的心愿单按钮状态")

        await interaction.edit_original_response(
            content=WISHLIST_ADDED_MESSAGE,
            embed=None,
            view=None,
        )

    @discord.ui.button(label="拒绝", style=discord.ButtonStyle.danger)
    async def disagree(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        """拒绝协议并关闭确认面板。"""
        # 拒绝协议并关闭确认面板。
        await interaction.response.edit_message(
            content="⚠️ 您已拒绝协议，资源未加入心愿单。",
            embed=None,
            view=None,
        )
