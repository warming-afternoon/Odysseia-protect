# -*- coding: utf-8 -*-
"""下载面板视图及心愿单操作按钮。"""

import logging
from typing import Sequence

import discord

from src.database.database import AsyncSessionLocal
from src.database.models import Resource
from src.ui.password_input_modal import DownloadResponseMode
from src.ui.resource_select import ResourceSelect
from src.ui.wishlist_ui import (
    WishlistConsentView,
    build_wishlist_consent_embed,
)

logger = logging.getLogger(__name__)

WISHLIST_USAGE = (
    "使用 `/心愿单`，或右键任意服务器消息 → Apps → “打开心愿单”查看。\n"
    "每页最多 8 项，页末 URL 可一键复制并粘贴到 "
    "SillyTavern 批量导入；链接失效后重新打开即可刷新。"
)


class ResourceSelectView(discord.ui.View):
    """版本选择、加入和移除心愿单的私密下载面板。"""

    def __init__(
        self,
        resources: Sequence[Resource],
        *,
        resource_list_embed: discord.Embed | None = None,
    ):
        super().__init__(timeout=14400.0)
        self.selected_resource_id: int | None = None
        self.add_item(
            ResourceSelect(
                resources,
                resource_list_embed=resource_list_embed,
            )
        )
        self.add_button = AddToWishlistButton()
        self.remove_button = RemoveFromWishlistButton()
        self.add_item(self.add_button)
        self.add_item(self.remove_button)

    def clear_authorized_selection(self):
        self.selected_resource_id = None
        self.add_button.disabled = True
        self.remove_button.disabled = True

    def set_wishlist_state(self, is_wishlisted: bool):
        self.add_button.disabled = is_wishlisted
        self.remove_button.disabled = not is_wishlisted

    async def authorize_selection(
        self,
        interaction: discord.Interaction,
        *,
        resource_id: int,
    ):
        service = getattr(interaction.client, "wishlist_service", None)
        if service is None:
            raise RuntimeError("Bot 未配置心愿单服务。")
        async with AsyncSessionLocal() as session:
            is_wishlisted = await service.is_wishlisted(
                session,
                user_id=interaction.user.id,
                resource_id=resource_id,
            )
        self.selected_resource_id = resource_id
        self.set_wishlist_state(is_wishlisted)

    async def on_timeout(self):
        self.stop()


class PublicResourceSelectView(discord.ui.View):
    """公开的限时防呆入口，只用于选择版本并创建私密下载面板。"""

    def __init__(
        self,
        resources: Sequence[Resource],
        *,
        resource_list_embed: discord.Embed,
        timeout: float = 60.0,
    ):
        super().__init__(timeout=timeout)
        resource_snapshot = tuple(resources)
        self.add_item(
            ResourceSelect(
                resource_snapshot,
                resource_list_embed=resource_list_embed,
                response_mode=DownloadResponseMode.CREATE_PRIVATE_PANEL,
                private_view_factory=lambda: ResourceSelectView(
                    resource_snapshot,
                    resource_list_embed=resource_list_embed,
                ),
            )
        )

    async def on_timeout(self):
        self.stop()


class AddToWishlistButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="加入心愿单",
            style=discord.ButtonStyle.success,
            disabled=True,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(self.view, ResourceSelectView):
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
                f"✅ 已加入心愿单！\n{WISHLIST_USAGE}",
                ephemeral=True,
            )


class RemoveFromWishlistButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="从心愿单中移除",
            style=discord.ButtonStyle.danger,
            disabled=True,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(self.view, ResourceSelectView):
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
                result = await service.remove(
                    session,
                    user_id=interaction.user.id,
                    resource_id=resource_id,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("从心愿单移除失败")
                await interaction.followup.send(
                    "❌ 从心愿单移除时发生内部错误。",
                    ephemeral=True,
                )
                return

        view.set_wishlist_state(False)
        await interaction.edit_original_response(view=view)
        message = (
            "✅ 已从心愿单中移除。"
            if result == "removed"
            else "ℹ️ 该资源本来就不在心愿单中。"
        )
        await interaction.followup.send(message, ephemeral=True)
