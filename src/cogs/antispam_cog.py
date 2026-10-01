from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from src.config import ANTISPAM_KEYWORDS
from src.database.database import AsyncSessionLocal
from src.ui.download_panel import build_download_panel
from src.enums.download_panel_mode import DownloadPanelMode

if TYPE_CHECKING:
    from main import OdysseiaProtect


class AntiSpamCog(commands.Cog):
    """检测下载关键词并提供短期公开入口。"""

    def __init__(self, bot: "OdysseiaProtect"):
        """记录机器人依赖供下载入口使用。"""
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """精确匹配关键词后发送限时公开下载面板。"""
        # 确保消息来自帖子，并且不是由机器人自己发出的
        if not isinstance(message.channel, discord.Thread) or message.author.bot:
            return

        # 检查是否精确匹配关键词
        if message.content.strip().lower() not in [
            kw.lower() for kw in ANTISPAM_KEYWORDS
        ]:
            return

        # 在短会话内取得当前页数据并构建公开入口。
        async with AsyncSessionLocal() as session:
            response_data = await build_download_panel(
                self.bot.download_service, session,
                source=message,
                panel_mode=DownloadPanelMode.PUBLIC_GATEWAY,
            )

        # 如果服务确定帖子无效或没有资源，则不响应
        if "view" not in response_data:
            return

        # 发送一个包含 Embed 和 View 的临时消息
        await message.channel.send(**response_data, delete_after=60)


async def setup(bot: "OdysseiaProtect"):
    """注册下载关键词监听组件。"""
    await bot.add_cog(AntiSpamCog(bot))
