# -*- coding: utf-8 -*-
"""
这个 Cog 模块负责处理所有与文件“下载”相关的命令和交互。
"""

import discord
from discord import app_commands
from discord.ext import commands

from typing import TYPE_CHECKING

from src.database.database import AsyncSessionLocal
from src.ui.download_panel import build_download_panel

if TYPE_CHECKING:
    from main import OdysseiaProtect

class DownloadCog(commands.Cog):
    """处理帖子资源下载命令及入口交互。"""

    def __init__(self, bot: "OdysseiaProtect"):
        """Cog 的构造函数。"""
        self.bot = bot

    @app_commands.command(name="下载", description="获取本帖资源的下载列表。")
    async def download(self, interaction: discord.Interaction):
        """查询资源第一页并发送私密下载面板。"""
        # 延迟响应，因为获取数据和构建视图可能需要时间
        await interaction.response.defer(ephemeral=True)

        # 在短会话内取得分页数据并构建下载面板。
        async with AsyncSessionLocal() as session:
            response_data = await build_download_panel(
                self.bot.download_service, session, source=interaction
            )

        # 数据库会话关闭后发送由 DTO 构建的响应。
        await interaction.followup.send(**response_data, ephemeral=True)

async def setup(bot: "OdysseiaProtect"):
    """将这个 Cog 注册到 Bot 实例中。"""
    await bot.add_cog(DownloadCog(bot))

    @app_commands.context_menu(name="打开下载面板")
    async def open_download_panel(
        interaction: discord.Interaction, message: discord.Message
    ):
        """从帖子内任意消息的 Apps 菜单打开私密下载面板。"""
        if not isinstance(interaction.channel, discord.Thread) or not isinstance(
            interaction.channel.parent, discord.ForumChannel
        ):
            await interaction.response.send_message(
                "❌ 此入口只能在论坛帖子中使用。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            response_data = await build_download_panel(
                bot.download_service, session, source=interaction
            )
        await interaction.followup.send(**response_data, ephemeral=True)

    bot.tree.add_command(open_download_panel)
