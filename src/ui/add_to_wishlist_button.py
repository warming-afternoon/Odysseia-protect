# -*- coding: utf-8 -*-
"""下载面板视图及心愿单操作按钮。"""

import logging

import discord

from src.enums.wishlist import WISHLIST_ADDED_MESSAGE
from src.database.database import AsyncSessionLocal
from src.ui.wishlist_ui import (
    WishlistConsentView,
    build_wishlist_consent_embed,
)

logger = logging.getLogger(__name__)

class AddToWishlistButton(discord.ui.Button):
    """将当前已授权资源加入心愿单。"""

    def __init__(self):
        """创建心愿单操作按钮。"""
        super().__init__(
            label="加入心愿单",
            style=discord.ButtonStyle.success,
            disabled=True,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        """串行处理心愿单操作，避免与翻页冲突。"""
        if self.view is None:
            return
        if self.view.state_lock.locked():
            await interaction.response.send_message("ℹ️ 面板正在更新，请稍后重试。", ephemeral=True)
            return
        async with self.view.state_lock:
            await self._handle(interaction)

    async def _handle(self, interaction: discord.Interaction):
        """处理当前已授权资源的心愿单操作。"""
        from src.ui.resource_select_view import ResourceSelectView

        # 校验选择状态后调用心愿单服务。
        if not isinstance(self.view, ResourceSelectView) or self not in self.view.children:
            return
        view = self.view
        resource_id = view.selected_resource_id
        if resource_id is None:
            await interaction.response.send_message(
                "❌ 请先选择并成功打开一个资源版本。",
                ephemeral=True,
            )
            return

        service = getattr(interaction.client, "wishlist_service", None)
        if service is None:
            await interaction.response.send_message(
                "❌ Bot 未配置心愿单服务。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            try:
                result = await service.add(
                    session,
                    user_id=interaction.user.id,
                    resource_id=resource_id,
                )
                if result in {"added", "already_added"}:
                    await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("加入心愿单失败")
                await interaction.followup.send(
                    "❌ 加入心愿单时发生内部错误。",
                    ephemeral=True,
                )
                return

        if result == "needs_consent":
            consent_view = WishlistConsentView(
                service=service,
                user_id=interaction.user.id,
                resource_id=resource_id,
                panel_view=view,
                panel_message=interaction.message,
            )
            await interaction.followup.send(
                embed=build_wishlist_consent_embed(),
                view=consent_view,
                ephemeral=True,
            )
            return
        if result == "not_found":
            view.clear_authorized_selection()
            await interaction.edit_original_response(view=view)
            await interaction.followup.send(
                "❌ 资源已不存在，无法加入心愿单。",
                ephemeral=True,
            )
            return

        view.set_wishlist_state(True)
        await interaction.edit_original_response(view=view)
        if result == "already_added":
            await interaction.followup.send(
                "ℹ️ 该资源已经在心愿单中。",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                WISHLIST_ADDED_MESSAGE,
                ephemeral=True,
            )
