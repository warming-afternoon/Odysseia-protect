import asyncio
import logging

import discord

from src.database.database import AsyncSessionLocal
from src.enums.download_response_mode import DownloadResponseMode
from src.exceptions.rate_limit_exceeded import RateLimitExceeded
from src.qo.resource_delivery_qo import ResourceDeliveryQo
from src.qo.resource_selection_qo import ResourceSelectionQo
from src.services.download_service import request_resource_delivery
from src.ui.download_embed_builder import DownloadEmbedBuilder
from src.ui.trace_consent_view import send_trace_consent
from src.utils.download_guard import (
    observe_download,
    send_download_error,
    send_rate_limit_message,
    update_operation,
)
from src.utils.safe_defer import safe_defer

logger = logging.getLogger(__name__)


class PasswordModal(discord.ui.Modal, title="请输入下载密码"):
    """一个用于在下载前验证密码的弹出式模态框。"""

    def __init__(
        self,
        *,
        resource_id: int,
        public_thread_id: int,
        resource_list_embed: discord.Embed,
        panel_view: discord.ui.View,
        response_mode: DownloadResponseMode = DownloadResponseMode.EDIT_PRIVATE_PANEL,
    ):
        """记录待验证资源与面板页快照。"""
        super().__init__(timeout=300)  # 5分钟超时
        self.resource_id = resource_id
        self.public_thread_id = public_thread_id
        self.resource_list_embed = resource_list_embed
        self.panel_view = panel_view
        self.page_snapshot = getattr(panel_view, "page_data", None)
        self.response_mode = response_mode

        self.password_input = discord.ui.TextInput(
            label="密码",
            style=discord.TextStyle.short,
            required=True,
            min_length=1,
            placeholder="请输入该资源版本对应的下载密码",
        )
        self.add_item(self.password_input)

    @observe_download("password_submit")
    async def on_submit(self, interaction: discord.Interaction):
        """串行验证密码并防止恢复已经翻页的面板。"""
        update_operation(resource_id=self.resource_id, thread_id=self.public_thread_id)
        is_public = self.response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL
        await safe_defer(interaction, thinking=is_public)
        lock = getattr(self.panel_view, "state_lock", None)
        if lock is not None and lock.locked():
            await send_download_error(
                interaction, "ℹ️ 面板正在更新，请稍后重试。", replace_response=is_public
            )
            return
        async with lock or asyncio.Lock():
            if self.panel_view.is_finished():
                await send_download_error(
                    interaction, "❌ 下载面板状态已失效，请重新选择资源。",
                    replace_response=is_public,
                )
                return
            if self.page_snapshot is not getattr(self.panel_view, "page_data", None):
                await send_download_error(
                    interaction, "❌ 页面已变化，请重新选择资源。", replace_response=is_public
                )
                return
            await self._handle_submit(interaction)

    async def _handle_submit(self, interaction: discord.Interaction):
        """查询最新资源和密码，通过后提供下载链接或溯源确认。"""
        is_public = self.response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL
        try:
            async with AsyncSessionLocal() as session:
                resource = await interaction.client.download_service.get_selected_resource(
                    session,
                    ResourceSelectionQo(
                        public_thread_id=self.public_thread_id, resource_id=self.resource_id
                    ),
                )
        except Exception:
            update_operation(outcome="failed")
            logger.exception("读取密码验证资源失败：resource=%s", self.resource_id)
            await send_download_error(
                interaction, "❌ 读取资源失败，请稍后重试。", replace_response=is_public
            )
            return
        if resource is None:
            await send_download_error(
                interaction, "❌ 资源已不存在，请刷新下载面板后重新选择。",
                replace_response=is_public,
            )
            return
        if resource.password and self.password_input.value != resource.password:
            update_operation(outcome="incorrect_password")
            embed = discord.Embed(
                title="❌ 密码错误",
                description="您输入的密码不正确，请重试。",
                color=discord.Color.red(),
            )
            await send_download_error(interaction, embed=embed, replace_response=is_public)
            return
        if resource.trace_enabled:
            update_operation(outcome="awaiting_consent")
            await send_trace_consent(
                interaction,
                resource=resource,
                resource_list_embed=self.resource_list_embed,
                panel_view=self.panel_view,
                response_mode=self.response_mode,
            )
            return

        try:
            download_service = getattr(interaction.client, "download_service", None)
            if download_service is None:
                raise RuntimeError("Bot 未配置下载服务。")
            delivery = await request_resource_delivery(
                interaction.client,
                resource,
                qo=ResourceDeliveryQo(user_id=interaction.user.id),
            )
            fresh_url = delivery.url

            # 触发下载事件，供下载计数器监听
            interaction.client.dispatch("resource_downloaded", resource)

            embed = DownloadEmbedBuilder.build_download_embed(resource, fresh_url)
            if hasattr(self.panel_view, "authorize_selection"):
                await self.panel_view.authorize_selection(
                    interaction,
                    resource_id=self.resource_id,
                )
            await interaction.edit_original_response(
                embeds=[embed, self.resource_list_embed],
                view=self.panel_view,
                attachments=[],
            )

        except RateLimitExceeded as error:
            await send_rate_limit_message(
                interaction,
                error,
                replace_response=self.response_mode
                is DownloadResponseMode.CREATE_PRIVATE_PANEL,
            )
        except Exception as e:
            update_operation(outcome="failed")
            logger.error(
                "为资源 %s 获取新下载链接失败：user_id=%s thread_id=%s "
                "source_message_id=%s guild_id=%s",
                self.resource_id,
                interaction.user.id,
                self.public_thread_id,
                resource.source_message_id,
                resource.guild_id or interaction.guild_id,
                exc_info=e,
            )
            error_message = (
                "❌ 抱歉，获取下载链接时发生错误。源文件可能已被删除或Bot无法访问。"
            )
            if self.response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL:
                await interaction.edit_original_response(
                    content=error_message,
                    embeds=[],
                    view=None,
                )
            else:
                await interaction.followup.send(error_message, ephemeral=True)
