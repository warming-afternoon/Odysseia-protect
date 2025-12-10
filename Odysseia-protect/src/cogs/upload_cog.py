# -*- coding: utf-8 -*-
"""
这个 Cog 模块负责处理所有与文件“上传”相关的命令和交互。
"""

# --- 导入必要的库 ---
import discord
from discord import app_commands
from discord.ext import commands

from typing import TYPE_CHECKING, Any

from src.database.database import AsyncSessionLocal

if TYPE_CHECKING:
    from main import OdysseiaProtect


# ===================================================================================
# 定义 UploadCog 类
# ===================================================================================


class UploadCog(commands.Cog):
    """
    这个 Cog 包含了 `/上传` 斜杠命令及其相关逻辑。
    相关的上下文菜单命令也在这个文件的 setup 函数中被动态注册。
    """

    def __init__(self, bot: "OdysseiaProtect"):
        self.bot = bot

    async def _handle_service_result(
        self, interaction: discord.Interaction, result: Any
    ):
        """
        一个辅助方法，用于统一处理来自 ResourceService 的返回结果。
        """
        if isinstance(result, dict):
            embed = result.get("embed")
            view = result.get("view")
            if embed:
                # 如果有 view 则一起发送，否则只发送 embed
                if view:
                    await interaction.response.send_message(
                        embed=embed, view=view, ephemeral=True
                    )
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                # 如果没有 embed，则视为错误
                await interaction.response.send_message(
                    "发生未知错误，无法显示响应。", ephemeral=True
                )
        elif isinstance(result, discord.ui.Modal):
            await interaction.response.send_modal(result)

    @app_commands.command(name="上传", description="上传一个新的文件版本。")
    @app_commands.describe(
        mode="选择上传类型。",
        file="【受保护文件】请在此处上传文件附件。",
        message_link="【普通文件】请在此处粘贴消息链接。",
    )
    @app_commands.choices(
        mode=[
            discord.app_commands.Choice(
                name="普通文件 (引用帖子内已有消息)",
                value="normal",
            ),
            discord.app_commands.Choice(
                name="受保护文件 (由 Bot 负责存储)",
                value="secure",
            ),
        ]
    )
    async def upload(
        self,
        interaction: discord.Interaction,
        mode: str,
        file: discord.Attachment | None = None,
        message_link: str | None = None,
    ):
        if mode == "secure" and not file:
            await interaction.response.send_message(
                "❌ **参数错误**\n您选择了 **受保护文件**，但未提供文件附件。",
                ephemeral=True,
            )
            return
        if mode == "normal" and not message_link:
            await interaction.response.send_message(
                "❌ **参数错误**\n您选择了 **普通文件**，但未提供消息链接。",
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            result = await self.bot.upload_service.handle_upload(
                session,
                interaction=interaction,
                mode=mode,
                file=file,
                message_link=message_link,
            )

        await self._handle_service_result(interaction, result)


# ===================================================================================
# Cog 的入口点函数 和 上下文菜单的定义与注册
# ===================================================================================


async def setup(bot: "OdysseiaProtect"):
    """
    加载 Cog 并手动注册与之关联的上下文菜单命令。
    这是确保在 Cog 中定义的逻辑能被顶级命令使用的健壮方法。
    """

    # --- 步骤 1: 定义上下文菜单的回调函数 ---
    @app_commands.context_menu(name="上传为普通文件")
    async def upload_normal_context_menu(
        interaction: discord.Interaction, message: discord.Message
    ):
        # 我们直接使用从 setup 传递进来的、类型正确的 bot 实例
        async with AsyncSessionLocal() as session:
            result = await bot.upload_service.handle_upload(
                session,
                interaction=interaction,
                mode="normal",
                file=None,
                message_link=message.jump_url,
            )

        # 复用 Cog 中的响应处理逻辑
        cog_instance = bot.get_cog("UploadCog")
        # 通过 isinstance 类型守卫，让 Pylance 知道 cog_instance 是 UploadCog 类型
        if isinstance(cog_instance, UploadCog):
            await cog_instance._handle_service_result(interaction, result)
        else:
            # 兜底错误处理：理论上不应该发生，因为我们马上就要注册它
            await interaction.response.send_message(
                "处理上传时发生内部错误。", ephemeral=True
            )

    # --- 步骤 2: 将 Cog 和手动定义的上下文菜单命令都添加到 Bot ---
    await bot.add_cog(UploadCog(bot))
    bot.tree.add_command(upload_normal_context_menu)
