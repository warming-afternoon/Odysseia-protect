# -*- coding: utf-8 -*-
"""
这个 Cog 模块负责处理所有与资源管理相关的命令。
"""

# --- 导入 ---
import discord
from discord import app_commands
from discord.ext import commands

from typing import TYPE_CHECKING

from src.database.database import AsyncSessionLocal
from src.services.management_service import ManagementService

if TYPE_CHECKING:
    from main import OdysseiaProtect


# ===================================================================================
# 定义 ManageCog 类
# ===================================================================================


class ManageCog(commands.Cog):
    """
    这个 Cog 包含了 `/管理` 命令，用于让作者管理自己上传的资源。
    """

    def __init__(self, bot: "OdysseiaProtect"):
        """Cog 的构造函数。"""
        self.bot = bot
        # 在 Cog 初始化时，就从 Bot 获取 Service 实例
        # 这是一个常见的依赖注入模式
        self.management_service: ManagementService = self.bot.management_service

    @app_commands.command(name="管理", description="管理您在此帖上传的资源。")
    async def manage(self, interaction: discord.Interaction):
        """
        处理 /管理 命令的核心函数。
        """
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            # 调用 Service 层来获取包含业务逻辑和 UI 组件的结果
            result = await self.management_service.handle_management_request(
                session, interaction=interaction
            )

        # 从结果中获取 Embed 和 View
        embed = result.get("embed")
        view = result.get("view")

        # 类型保护：确保 embed 存在后再发送
        if embed:
            # 如果 view 存在，则一起发送
            if view:
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            # 这是一个回退，理论上不应该发生
            await interaction.followup.send(
                "发生了一个未知错误，无法生成响应。", ephemeral=True
            )


# ===================================================================================
# Cog 的入口点函数
# ===================================================================================


async def setup(bot: "OdysseiaProtect"):
    """将这个 Cog 注册到 Bot 实例中。"""
    await bot.add_cog(ManageCog(bot))
