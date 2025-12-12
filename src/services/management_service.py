# -*- coding: utf-8 -*-
"""
管理服务，负责处理资源管理相关的业务逻辑。
"""

import logging
from typing import Any, Optional

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Resource, UploadMode
from src.services.base import BaseService
from src.ui.management_ui import ManagementView
from src.utils.formatting import format_resource_list

logger = logging.getLogger(__name__)


class ManagementService(BaseService):
    """封装了所有与资源管理相关的业务逻辑。"""

    async def handle_management_request(
        self, session: AsyncSession, *, interaction: discord.Interaction
    ) -> dict[str, Any]:
        """处理 /管理 命令的请求，返回管理视图。"""
        if not interaction.channel or not isinstance(
            interaction.channel, (discord.TextChannel, discord.Thread)
        ):
            embed = discord.Embed(
                title="❌ 操作无效",
                description="此命令只能在帖子或文本频道中使用。",
                color=discord.Color.red(),
            )
            return {"embed": embed}

        thread_model = await self.thread_repo.get_by_public_thread_id(
            session, public_thread_id=interaction.channel.id
        )
        if not thread_model:
            embed = discord.Embed(
                title="📂 暂无资源",
                description="此帖没有任何资源可供管理。",
                color=discord.Color.blue(),
            )
            return {"embed": embed}

        # 权限检查：只有帖子的作者才能管理资源
        if thread_model.author_id != interaction.user.id:
            embed = discord.Embed(
                title="🚫 权限不足",
                description="抱歉，只有本帖的作者才能管理这里的资源。",
                color=discord.Color.red(),
            )
            return {"embed": embed}

        # 获取该帖子的所有资源
        resources = await self.resource_repo.get_by_thread_id(
            session, thread_id=thread_model.id
        )

        embed = discord.Embed(
            title="🛠️ 资源管理",
            description="在这里管理此帖的资源和设置。",
            color=discord.Color.orange(),
        )

        # # 添加反应墙状态字段
        # reaction_status = "已开启" if thread_model.reaction_required else "已关闭"
        # emoji_info = (
        #     f"自定义表情: {thread_model.reaction_emoji}"
        #     if thread_model.reaction_emoji
        #     else "任意表情"
        # )
        # reaction_desc = f"用户需要先对本帖的做出反应，然后才能下载 **受保护资源**。\n**要求**: {emoji_info}"
        # embed.add_field(
        #     name=f"🔒 反应墙状态: {reaction_status}",
        #     value=reaction_desc,
        #     inline=False,
        # )

        quick_mode_status = "已开启" if thread_model.quick_mode_enabled else "已关闭"
        quick_mode_desc = "开启后，使用 App 命令转存的资源将 **自动删除** 原始消息。"
        embed.add_field(
            name=f"⚡ 快捷模式: {quick_mode_status}",
            value=quick_mode_desc,
            inline=False,
        )

        if not resources:
            embed.add_field(
                name="资源列表",
                value="此帖还没有任何资源。",
                inline=False,
            )
        else:
            # 按模式分组资源
            secure_resources = [
                r for r in resources if r.upload_mode == UploadMode.SECURE
            ]
            normal_resources = [
                r for r in resources if r.upload_mode == UploadMode.NORMAL
            ]

            embed.add_field(
                name="🔒 受保护资源",
                value=format_resource_list(secure_resources, source=interaction),
                inline=False,
            )
            embed.add_field(
                name="📄 资源",
                value=format_resource_list(
                    normal_resources, is_normal_mode=True, source=interaction
                ),
                inline=False,
            )

        view = ManagementView(resources, self, interaction, thread_model)
        return {"embed": embed, "view": view}

    async def update_resource(
        self,
        session: AsyncSession,
        *,
        resource_id: int,
        version_info: str,
        password: Optional[str],
    ) -> Optional[Resource]:
        """根据 ID 更新一个资源的信息。"""
        db_obj = await self.resource_repo.get(session, id=resource_id)
        if not db_obj:
            return None

        update_data = {"version_info": version_info, "password": password}
        updated_resource = await self.resource_repo.update(
            session, db_obj=db_obj, obj_in=update_data
        )
        return updated_resource

    async def delete_resource(self, session: AsyncSession, *, resource_id: int) -> bool:
        """
        根据 ID 删除一个资源。
        此操作会先尝试删除 Discord 上的源消息，然后再删除数据库记录。
        """
        # 步骤 1: 获取完整的资源信息，包括其所属的帖子
        resource_to_delete = await self.resource_repo.get_with_thread(
            session, id=resource_id
        )

        if not resource_to_delete:
            logger.warning(f"尝试删除一个不存在的资源，ID: {resource_id}")
            return False

        # 步骤 2: 如果是受保护文件，尝试删除 Discord 上的源文件消息 (尽力而为)
        if resource_to_delete.upload_mode == UploadMode.SECURE:
            try:
                # 受保护文件的消息一定在仓库频道
                channel_id = resource_to_delete.thread.warehouse_thread_id
                if not channel_id:
                    raise ValueError("受保护文件资源缺少仓库帖子ID")

                source_channel = await self.bot.fetch_channel(channel_id)
                assert isinstance(source_channel, (discord.TextChannel, discord.Thread))

                source_message = await source_channel.fetch_message(
                    resource_to_delete.source_message_id
                )
                await source_message.delete()
                logger.info(
                    f"成功从 Discord 删除受保护文件源消息 {resource_to_delete.source_message_id}"
                )
            except (
                discord.NotFound,
                discord.Forbidden,
                AssertionError,
                ValueError,
            ) as e:
                logger.warning(
                    f"无法删除受保护文件源消息 {resource_to_delete.source_message_id}。"
                    f"它可能已被手动删除或Bot权限不足。错误: {e}"
                )
            except Exception as e:
                logger.error(
                    f"删除受保护文件源消息 {resource_to_delete.source_message_id} 时发生未知错误。",
                    exc_info=e,
                )
        else:
            # 对于普通文件，我们只记录日志，绝不删除用户自己的消息
            logger.info(
                f"正在删除普通文件资源 {resource_id} 的数据库记录。"
                f"引用的用户消息 {resource_to_delete.source_message_id} 将被保留。"
            )

        # 步骤 3: 从数据库中删除记录
        deleted_obj = await self.resource_repo.remove(session, id=resource_id)
        if deleted_obj:
            logger.info(f"成功从数据库删除资源 {resource_id}")
        return deleted_obj is not None
