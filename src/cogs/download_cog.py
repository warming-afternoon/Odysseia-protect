# -*- coding: utf-8 -*-
"""
这个 Cog 模块负责处理所有与文件“下载”相关的命令和交互。
"""

# --- 导入 ---
import discord
from discord import app_commands
from discord.ext import commands

from typing import TYPE_CHECKING

from src.database.database import AsyncSessionLocal

if TYPE_CHECKING:
    from main import OdysseiaProtect

# ===================================================================================
# 定义 DownloadCog 类
# ===================================================================================


class DownloadCog(commands.Cog):
    """
    这个 Cog 包含了 `/下载` 命令，用于让用户浏览和下载当前帖子的资源。
    """

    def __init__(self, bot: "OdysseiaProtect"):
        """Cog 的构造函数。"""
        self.bot = bot

    @app_commands.command(name="下载", description="获取本帖资源的下载列表。")
    async def download(self, interaction: discord.Interaction):
        """
        处理 /下载 命令的核心函数。

        它将调用 Service 层来获取资源列表并构建一个交互式的选择菜单。
        """
        # 延迟响应，因为获取数据和构建视图可能需要时间
        await interaction.response.defer(ephemeral=True)

        # 调用 Service 层来处理下载请求
        async with AsyncSessionLocal() as session:
            response_data = await self.bot.download_service.handle_download_request(
                session, source=interaction
            )

        # 使用关键字参数解包来发送响应
        # 如果 "view" 不在字典中，它就不会被作为参数传递
        await interaction.followup.send(**response_data, ephemeral=True)


# ===================================================================================
# Cog 的入口点函数
# ===================================================================================


async def setup(bot: "OdysseiaProtect"):
    """将这个 Cog 注册到 Bot 实例中。"""
    await bot.add_cog(DownloadCog(bot))
