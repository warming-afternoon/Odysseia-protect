# -*- coding: utf-8 -*-
"""
这个 Cog 模块负责处理所有与文件“上传”相关的命令和交互。
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

from typing import TYPE_CHECKING, Any

from src.database.database import AsyncSessionLocal
from src.utils.auth import assert_thread_author

if TYPE_CHECKING:
    from main import OdysseiaProtect

logger = logging.getLogger(__name__)

class UploadCog(commands.Cog):
    """
    这个 Cog 包含了 `/上传` 斜杠命令及其相关逻辑。
    相关的上下文菜单命令也在这个文件的 setup 函数中被动态注册。
    """

    def __init__(self, bot: "OdysseiaProtect"):
        self.bot = bot

    upload_group = app_commands.Group(
        name="上传",
        description="上传一个新的文件版本。",
    )

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

    async def _start_upload(
        self,
        interaction: discord.Interaction,
        *,
        mode: str,
        file: discord.Attachment | None = None,
        message_link: str | None = None,
    ):
        """执行两个上传子命令共用的校验、鉴权与响应流程。"""
        if not isinstance(interaction.channel, discord.Thread) or not isinstance(
            interaction.channel.parent, discord.ForumChannel
        ):
            await interaction.response.send_message(
                "❌ **操作无效**\n此命令只能在论坛帖子中使用。",
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            # 权限校验：仅允许帖主上传
            if not await assert_thread_author(session, interaction=interaction):
                return

            # 服务层处理上传
            result = await self.bot.upload_service.handle_upload(
                session,
                interaction=interaction,
                mode=mode,
                file=file,
                message_link=message_link,
            )

        # For deferred interactions, we need to use followup.send
        if interaction.response.is_done():
            if isinstance(result, dict):
                embed = result.get("embed")
                view = result.get("view")
                if embed:
                    if view and isinstance(view, discord.ui.View):
                        await interaction.followup.send(
                            embed=embed, view=view, ephemeral=True
                        )
                    else:
                        await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send("发生未知错误。", ephemeral=True)
            elif isinstance(result, discord.ui.Modal):
                # Modals cannot be sent as followups. This path should not be hit with defer.
                pass
        else:
            await self._handle_service_result(interaction, result)

    @upload_group.command(
        name="普通文件",
        description="引用帖子内已有消息，登记一个普通资源版本。",
    )
    @app_commands.describe(message_link="帖子内资源消息的链接。")
    async def upload_normal(
        self,
        interaction: discord.Interaction,
        message_link: str,
    ):
        await self._start_upload(
            interaction,
            mode="normal",
            file=None,
            message_link=message_link,
        )

    @upload_group.command(
        name="受保护文件",
        description="将附件交给 Bot 存储为受保护资源。",
    )
    @app_commands.describe(file="需要保护的文件附件。")
    async def upload_secure(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
    ):
        await self._start_upload(
            interaction,
            mode="secure",
            file=file,
            message_link=None,
        )


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
        if not isinstance(interaction.channel, discord.Thread) or not isinstance(
            interaction.channel.parent, discord.ForumChannel
        ):
            await interaction.response.send_message(
                "❌ **操作无效**\n此命令只能在论坛帖子中使用。",
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            # 权限校验：仅允许帖主上传
            if not await assert_thread_author(session, interaction=interaction):
                return

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
            if interaction.response.is_done():
                if isinstance(result, dict):
                    embed = result.get("embed")
                    view = result.get("view")
                    if embed:
                        if view and isinstance(view, discord.ui.View):
                            await interaction.followup.send(
                                embed=embed, view=view, ephemeral=True
                            )
                        else:
                            await interaction.followup.send(embed=embed, ephemeral=True)
                    else:
                        await interaction.followup.send(
                            "发生未知错误。", ephemeral=True
                        )
            else:
                await cog_instance._handle_service_result(interaction, result)
        else:
            # 兜底错误处理：理论上不应该发生，因为我们马上就要注册它
            await interaction.followup.send("处理上传时发生内部错误。", ephemeral=True)

    @app_commands.context_menu(name="上传为受保护资源")
    async def upload_secure_context_menu(
        interaction: discord.Interaction, message: discord.Message
    ):
        if not isinstance(interaction.channel, discord.Thread) or not isinstance(
            interaction.channel.parent, discord.ForumChannel
        ):
            await interaction.response.send_message(
                "❌ **操作无效**\n此命令只能在论坛帖子中使用。",
                ephemeral=True,
            )
            return

        if not message.attachments:
            await interaction.response.send_message(
                "❌ **操作无效**\n您选择的消息中没有任何附件可供保护。",
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            # 权限校验：仅允许帖主上传
            if not await assert_thread_author(session, interaction=interaction):
                return
            result = await bot.upload_service.handle_secure_upload_from_message(
                session,
                interaction=interaction,
                message=message,
            )

        cog_instance = bot.get_cog("UploadCog")
        if isinstance(cog_instance, UploadCog):
            if isinstance(result, discord.ui.Modal):
                await interaction.response.send_modal(result)
            elif isinstance(result, dict):
                # 如果返回字典，说明是权限错误
                embed = result.get("embed")
                if embed:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(
                        "发生未知错误。", ephemeral=True
                    )
        else:
            await interaction.response.send_message(
                "处理上传时发生内部错误。", ephemeral=True
            )

    # --- 步骤 2: 将 Cog 和手动定义的上下文菜单命令都添加到 Bot ---
    await bot.add_cog(UploadCog(bot))
    bot.tree.add_command(upload_normal_context_menu)
    bot.tree.add_command(upload_secure_context_menu)
