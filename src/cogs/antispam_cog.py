from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from src.config import ANTISPAM_KEYWORDS
from src.database.database import AsyncSessionLocal

if TYPE_CHECKING:
    from main import OdysseiaProtect


class AntiSpamCog(commands.Cog):
    """
    用于防止滥用和引导用户的 Cog.
    """

    def __init__(self, bot: "OdysseiaProtect"):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        监听帖子中的消息，如果检测到特定关键词，则发送一个临时的下载面板。
        """
        # 确保消息来自帖子，并且不是由机器人自己发出的
        if not isinstance(message.channel, discord.Thread) or message.author.bot:
            return

        # 检查是否精确匹配关键词
        if message.content.strip().lower() not in [
            kw.lower() for kw in ANTISPAM_KEYWORDS
        ]:
            return

        # 调用通用的 download_service 来处理请求
        async with AsyncSessionLocal() as session:
            response_data = await self.bot.download_service.handle_download_request(
                session, source=message
            )

        # 如果服务确定帖子无效或没有资源，则不响应
        if "view" not in response_data:
            return

        # 发送一个包含 Embed 和 View 的临时消息
        await message.channel.send(**response_data, delete_after=60)


async def setup(bot: "OdysseiaProtect"):
    """
    设置 AntiSpamCog.
    """
    await bot.add_cog(AntiSpamCog(bot))
