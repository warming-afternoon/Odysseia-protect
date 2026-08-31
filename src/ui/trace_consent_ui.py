"""动态溯源下载前的明确告知与确认。"""

from __future__ import annotations

import io
import logging

import discord

from src.config import TRACE_DOWNLOAD_CONSENT_TEXT
from src.dto.resource_dto import ResourceDTO

logger = logging.getLogger(__name__)


def build_trace_consent_embed(resource: ResourceDTO) -> discord.Embed:
    return discord.Embed(
        title="🔎 动态溯源告知",
        description=(
            f"资源 `{resource.filename or '未命名文件'}` 已由作者开启动态溯源。\n\n"
            + TRACE_DOWNLOAD_CONSENT_TEXT
        ),
        color=discord.Color.orange(),
    )


class TraceConsentView(discord.ui.View):
    def __init__(
        self,
        *,
        resource: ResourceDTO,
        resource_list_embed: discord.Embed,
        panel_view: discord.ui.View,
        user_id: int,
    ):
        super().__init__(timeout=300)
        self.resource = resource
        self.resource_list_embed = resource_list_embed
        self.panel_view = panel_view
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "❌ 这不是您的溯源资源确认面板。", ephemeral=True
        )
        return False

    @discord.ui.button(label="同意并生成", style=discord.ButtonStyle.success)
    async def agree(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            service = getattr(interaction.client, "download_service", None)
            if service is None:
                raise RuntimeError("Bot 未配置下载服务。")
            delivery = await service.fetch_delivery(
                self.resource, user_id=interaction.user.id
            )
            interaction.client.dispatch("resource_downloaded", self.resource)
            if hasattr(self.panel_view, "authorize_selection"):
                await self.panel_view.authorize_selection(
                    interaction, resource_id=self.resource.id
                )
            embed = service.build_delivery_embed(self.resource, delivery)
            attachments = []
            if delivery.file_data is not None:
                attachments.append(
                    discord.File(
                        io.BytesIO(delivery.file_data), filename=delivery.filename
                    )
                )
            await interaction.edit_original_response(
                content=None,
                embeds=[embed, self.resource_list_embed],
                view=self.panel_view,
                attachments=attachments,
            )
        except Exception:
            logger.exception("生成动态溯源交付失败：resource=%s", self.resource.id)
            await interaction.edit_original_response(
                content="❌ 生成包括溯源水印的资源文件时失败，请稍后重试。",
                embeds=[],
                view=None,
                attachments=[],
            )

    @discord.ui.button(label="拒绝", style=discord.ButtonStyle.danger)
    async def disagree(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(
            content="⚠️ 您已拒绝动态溯源，不能下载本资源。",
            embed=None,
            view=None,
        )


async def send_trace_consent(
    interaction: discord.Interaction,
    *,
    resource: ResourceDTO,
    resource_list_embed: discord.Embed,
    panel_view: discord.ui.View,
) -> None:
    await interaction.response.send_message(
        embed=build_trace_consent_embed(resource),
        view=TraceConsentView(
            resource=resource,
            resource_list_embed=resource_list_embed,
            panel_view=panel_view,
            user_id=interaction.user.id,
        ),
        ephemeral=True,
    )
