import logging
from enum import Enum

import discord

from src.dto.resource_dto import ResourceDTO

logger = logging.getLogger(__name__)


class DownloadResponseMode(Enum):
    """下载选择后的响应目标。"""

    EDIT_PRIVATE_PANEL = "edit_private_panel"
    CREATE_PRIVATE_PANEL = "create_private_panel"


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
        super().__init__(timeout=300)  # 5分钟超时
        self.resource = resource
        self.resource_list_embed = resource_list_embed
        self.panel_view = panel_view
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
        """当用户提交密码后，验证密码并提供下载链接或错误信息。"""
        if self.password_input.value != self.resource.password:
            embed = discord.Embed(
                title="❌ 密码错误",
                description="您输入的密码不正确，请重试。",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
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

            embed = download_service.build_download_embed(self.resource, fresh_url)
            if hasattr(self.panel_view, "authorize_selection"):
                await self.panel_view.authorize_selection(
                    interaction,
                    resource_id=self.resource.id,
                )
            await interaction.edit_original_response(
                embeds=[embed, self.resource_list_embed],
                view=self.panel_view,
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
