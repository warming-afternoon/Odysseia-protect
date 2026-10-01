import asyncio
import logging
from src.enums.download_response_mode import DownloadResponseMode
from src.ui.download_embed_builder import DownloadEmbedBuilder

import discord

from src.dto.resource_dto import ResourceDTO
from src.ui.trace_consent_view import send_trace_consent

logger = logging.getLogger(__name__)


class PasswordModal(discord.ui.Modal, title="请输入下载密码"):
    """一个用于在下载前验证密码的弹出式模态框。"""

    def __init__(
        self,
        resource: ResourceDTO,
        *,
        resource_list_embed: discord.Embed,
        panel_view: discord.ui.View,
        response_mode: DownloadResponseMode = DownloadResponseMode.EDIT_PRIVATE_PANEL,
    ):
        """记录待验证资源与面板页快照。"""
        super().__init__(timeout=300)  # 5分钟超时
        self.resource = resource
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

    async def on_submit(self, interaction: discord.Interaction):
        """串行验证密码并防止恢复已经翻页的面板。"""
        # 已在翻页的面板及时提示，避免等待时交互过期。
        lock = getattr(self.panel_view, "state_lock", None)
        if lock is not None and lock.locked():
            await interaction.response.send_message("ℹ️ 面板正在更新，请稍后重试。", ephemeral=True)
            return
        async with lock or asyncio.Lock():
            if self.page_snapshot is not getattr(self.panel_view, "page_data", None):
                await interaction.response.send_message("❌ 页面已变化，请重新选择资源。", ephemeral=True)
                return
            await self._handle_submit(interaction)

    async def _handle_submit(self, interaction: discord.Interaction):
        """验证密码并提供下载链接或溯源确认。"""
        # 密码验证失败时保留原页与菜单。
        if self.password_input.value != self.resource.password:
            embed = discord.Embed(
                title="❌ 密码错误",
                description="您输入的密码不正确，请重试。",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if self.resource.trace_enabled:
            await send_trace_consent(
                interaction,
                resource=self.resource,
                resource_list_embed=self.resource_list_embed,
                panel_view=self.panel_view,
            )
            return

        # 公开防呆面板不能被原地更新；密码通过后创建新的私密响应。
        if self.response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL:
            await interaction.response.defer(ephemeral=True, thinking=True)
        else:
            await interaction.response.defer()

        try:
            download_service = getattr(interaction.client, "download_service", None)
            if download_service is None:
                raise RuntimeError("Bot 未配置下载服务。")
            fresh_url = await download_service.fetch_fresh_url(self.resource)

            # 触发下载事件，供下载计数器监听
            interaction.client.dispatch("resource_downloaded", self.resource)

            embed = DownloadEmbedBuilder.build_download_embed(self.resource, fresh_url)
            if hasattr(self.panel_view, "authorize_selection"):
                await self.panel_view.authorize_selection(
                    interaction,
                    resource_id=self.resource.id,
                )
            await interaction.edit_original_response(
                embeds=[embed, self.resource_list_embed],
                view=self.panel_view,
                attachments=[],
            )

        except Exception as e:
            logger.error(f"为资源 {self.resource.id} 获取新下载链接失败", exc_info=e)
            error_message = (
                "❌ 抱歉，获取下载链接时发生错误。"
                "源文件可能已被删除或Bot无法访问。"
            )
            if self.response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL:
                await interaction.edit_original_response(
                    content=error_message,
                    embeds=[],
                    view=None,
                )
            else:
                await interaction.followup.send(error_message, ephemeral=True)
