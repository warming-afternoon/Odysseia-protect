"""论坛帖子内的常驻下载入口。"""

import discord

from src.database.database import AsyncSessionLocal


class DownloadEntryView(discord.ui.View):
    """重启后仍可响应的公开入口；具体下载面板始终为 Ephemeral。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="获取角色卡",
        emoji="📥",
        style=discord.ButtonStyle.primary,
        custom_id="odysseia-protect:open-download-panel",
    )
    async def open_download_panel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not isinstance(interaction.channel, discord.Thread) or not isinstance(
            interaction.channel.parent, discord.ForumChannel
        ):
            await interaction.response.send_message(
                "❌ 此入口只能在论坛帖子中使用。", ephemeral=True
            )
            return

        bot = interaction.client
        if not hasattr(bot, "download_service"):
            await interaction.response.send_message(
                "❌ 下载服务尚未准备完成，请稍后重试。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            response_data = await bot.download_service.handle_download_request(
                session, source=interaction
            )
        await interaction.followup.send(**response_data, ephemeral=True)


def build_download_entry_embed() -> discord.Embed:
    """公开消息只提供稳定入口，不保存任何有时效性的 CDN URL。"""
    return discord.Embed(
        title="📦 角色卡下载",
        description=(
            "点击下方按钮打开仅自己可见的下载面板。\n"
            "Bot 会在每次下载时获取最新链接，链接不会公开显示在帖子中。"
        ),
        color=discord.Color.blue(),
    )
