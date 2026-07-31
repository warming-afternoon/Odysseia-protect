"""心愿单斜杠命令与消息右键入口。"""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from src.database.database import AsyncSessionLocal

if TYPE_CHECKING:
    from main import OdysseiaProtect


class WishlistCog(commands.Cog):
    def __init__(self, bot: "OdysseiaProtect"):
        self.bot = bot

    async def _send_wishlist(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ 心愿单只能在服务器频道中打开。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            render = await self.bot.wishlist_service.build_render(
                session,
                user_id=interaction.user.id,
                page=1,
            )

        kwargs = {
            "view": render.view,
            "ephemeral": True,
        }
        if render.file is not None:
            kwargs["file"] = render.file
        await interaction.followup.send(**kwargs)

    @app_commands.command(name="心愿单", description="私密查看并批量导出您的心愿单。")
    async def wishlist(self, interaction: discord.Interaction):
        await self._send_wishlist(interaction)


async def setup(bot: "OdysseiaProtect"):
    cog = WishlistCog(bot)
    await bot.add_cog(cog)

    @app_commands.context_menu(name="打开心愿单")
    async def open_wishlist(
        interaction: discord.Interaction,
        message: discord.Message,
    ):
        # 被右键的消息只作为 Apps 入口，不参与心愿单查询。
        await cog._send_wishlist(interaction)

    bot.tree.add_command(open_wishlist)
