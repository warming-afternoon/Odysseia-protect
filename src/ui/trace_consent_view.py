"""动态溯源下载前的明确告知与确认。"""

from __future__ import annotations

import io
import logging

import discord

from src.config import TRACE_DOWNLOAD_CONSENT_TEXT
from src.dto.resource_dto import ResourceDTO
from src.enums.download_response_mode import DownloadResponseMode
from src.exceptions.rate_limit_exceeded import RateLimitExceeded
from src.qo.resource_delivery_qo import ResourceDeliveryQo
from src.services.download_service import request_resource_delivery
from src.ui.download_embed_builder import DownloadEmbedBuilder
from src.utils.download_guard import (
    observe_download,
    send_rate_limit_message,
    update_operation,
)

logger = logging.getLogger(__name__)


def build_trace_consent_embed(resource: ResourceDTO) -> discord.Embed:
    """构建下载动态溯源资源前的告知内容。"""
    return discord.Embed(
        title="🔎 动态溯源告知",
        description=(
            f"资源 `{resource.filename or '未命名文件'}` 已由作者开启动态溯源。\n\n"
            + TRACE_DOWNLOAD_CONSENT_TEXT
        ),
        color=discord.Color.orange(),
    )


class TraceConsentView(discord.ui.View):
    """等待当前用户确认动态溯源下载。"""

    def __init__(
        self,
        *,
        resource: ResourceDTO,
        resource_list_embed: discord.Embed,
        panel_view: discord.ui.View,
        user_id: int,
    ):
        """保存独立资源详情与对应的个人下载面板。"""
        super().__init__(timeout=300)
        self.resource = resource
        self.resource_list_embed = resource_list_embed
        self.panel_view = panel_view
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """只允许发起下载的用户确认。"""
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "❌ 这不是您的溯源资源确认面板。", ephemeral=True
        )
        return False

    @discord.ui.button(label="同意并生成", style=discord.ButtonStyle.success)
    @observe_download("trace_consent_agree")
    async def agree(self, interaction: discord.Interaction, button: discord.ui.Button):
        """生成个性化文件并展示当前页的个人下载面板。"""
        # 确认视图持有独立面板，不影响原下载面板的翻页。
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            # 每次交付拥有独立面板，重复点击也不共享消息状态。
            panel_view = self.panel_view
            if hasattr(panel_view, "create_private_view"):
                panel_view = panel_view.create_private_view(user_id=interaction.user.id)
            service = getattr(interaction.client, "download_service", None)
            if service is None:
                raise RuntimeError("Bot 未配置下载服务。")
            delivery = await request_resource_delivery(
                interaction.client,
                self.resource,
                qo=ResourceDeliveryQo(user_id=interaction.user.id),
            )
            interaction.client.dispatch("resource_downloaded", self.resource)
            if hasattr(panel_view, "authorize_selection"):
                await panel_view.authorize_selection(
                    interaction, resource_id=self.resource.id
                )
            embed = DownloadEmbedBuilder.build_delivery_embed(self.resource, delivery)
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
                view=panel_view,
                attachments=attachments,
            )
        except RateLimitExceeded as error:
            await send_rate_limit_message(interaction, error, replace_response=True)
        except Exception:
            update_operation(outcome="failed")
            logger.exception("生成动态溯源交付失败：resource=%s", self.resource.id)
            await interaction.edit_original_response(
                content="❌ 生成包括溯源水印的资源文件时失败，请稍后重试。",
                embeds=[],
                view=None,
                attachments=[],
            )

    @discord.ui.button(label="拒绝", style=discord.ButtonStyle.danger)
    @observe_download("trace_consent_decline")
    async def disagree(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """拒绝动态溯源下载并关闭确认面板。"""
        update_operation(
            resource_id=self.resource.id,
            thread_id=self.resource.public_thread_id,
            outcome="declined",
        )
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
    response_mode: DownloadResponseMode = DownloadResponseMode.EDIT_PRIVATE_PANEL,
) -> None:
    """发送私密告知并复制当前页，隔离后续下载面板状态。"""
    # 溯源下载回复属于另一条消息，使用独立视图避免状态互相覆盖。
    if hasattr(panel_view, "create_private_view"):
        panel_view = panel_view.create_private_view(user_id=interaction.user.id)
        resource_list_embed = panel_view.resource_list_embed
    response = dict(
        embed=build_trace_consent_embed(resource),
        view=TraceConsentView(
            resource=resource,
            resource_list_embed=resource_list_embed,
            panel_view=panel_view,
            user_id=interaction.user.id,
        ),
    )
    if not interaction.response.is_done():
        await interaction.response.send_message(**response, ephemeral=True)
    elif response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL:
        await interaction.edit_original_response(content=None, **response)
    else:
        await interaction.followup.send(**response, ephemeral=True)
