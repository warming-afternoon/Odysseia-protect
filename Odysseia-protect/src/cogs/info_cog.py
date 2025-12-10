import discord
from discord.ext import commands
from discord import app_commands

from src.config import USER_MANUAL_TEXT


class InfoCog(commands.Cog):
    """
    一个包含各种信息展示命令的 Cog
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 内容现在直接从配置中导入
        self.manual_content = USER_MANUAL_TEXT

    @app_commands.command(name="使用手册", description="显示 Bot 的详细使用手册。")
    async def manual(self, interaction: discord.Interaction):
        """
        显示 Bot 的使用手册
        """
        embed = discord.Embed(
            title="Odysseia Protect Bot 使用手册",
            description=self.manual_content,
            color=discord.Color.blue(),
        )
        embed.set_footer(text="本手册将为您介绍所有核心功能和使用方法。")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(InfoCog(bot))
