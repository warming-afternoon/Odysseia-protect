# -*- coding: utf-8 -*-
"""
管理服务，负责处理资源管理相关的业务逻辑。
"""

import io
import logging
from typing import Any, Optional

import discord
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Resource, UploadMode
from src.services.base import BaseService
from src.ui.management_ui import ManagementView
from src.utils.formatting import format_resource_list_chunks

logger = logging.getLogger(__name__)


class ManagementService(BaseService):
    """封装了所有与资源管理相关的业务逻辑。"""

    async def handle_management_request(
        self, session: AsyncSession, *, interaction: discord.Interaction,
        selected_resource_id: int | None = None,
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

            # 处理受保护资源的分页显示
            secure_chunks = format_resource_list_chunks(secure_resources, source=interaction)
            for i, chunk in enumerate(secure_chunks):
                name = "🔒 受保护资源" if i == 0 else "🔒 受保护资源 (续)"
                embed.add_field(name=name, value=chunk, inline=False)

            # 处理普通资源的分页显示
            normal_chunks = format_resource_list_chunks(normal_resources, is_normal_mode=True, source=interaction)
            for i, chunk in enumerate(normal_chunks):
                name = "📄 资源" if i == 0 else "📄 资源 (续)"
                embed.add_field(name=name, value=chunk, inline=False)

        view = ManagementView(
            resources, self, interaction, thread_model,
            selected_resource_id=selected_resource_id,
        )
        return {"embed": embed, "view": view}

    async def replace_resource_source(
        self,
        session: AsyncSession,
        *,
        resource_id: int,
        interaction: discord.Interaction,
        attachment: discord.Attachment,
        expected_source_message_id: int,
    ) -> str:
        """提交换源事务后才清理旧文件；调用方必须提供独立会话。"""
        from src.services.upload_service import UploadService

        resource = await self.resource_repo.get_with_thread(session, id=resource_id)
        if resource is None:
            raise ValueError("资源已被删除，请刷新管理面板。")
        thread = resource.thread
        if (
            thread.author_id != interaction.user.id
            or thread.public_thread_id != interaction.channel_id
            or thread.guild_id not in (None, interaction.guild_id)
        ):
            raise ValueError("只有本帖作者能在原帖中换源。")
        if resource.upload_mode != UploadMode.SECURE:
            raise ValueError("仅受保护资源支持换源。")
        if resource.source_message_id != expected_source_message_id:
            raise ValueError("资源已被换源，请刷新面板后重试。")
        warehouse_id = thread.warehouse_thread_id
        if not warehouse_id:
            raise ValueError("找不到资源的私密仓库。")
        thread_id = thread.id
        trace_enabled = resource.trace_enabled
        # Discord 的 filename 可能移除中文；title 保留上传时的名称。
        filename = attachment.title or attachment.filename

        if trace_enabled:
            trace = getattr(self.bot, "traceability_service", None)
            if trace is None or not trace.available:
                raise ValueError("动态溯源服务不可用，暂时无法换源。")
            UploadService._validate_trace_attachment_sizes([attachment])
            data = await attachment.read()
            UploadService._validate_trace_source_data(filename, data)
            trace.validate_character_card(filename, data)
            upload_file = discord.File(io.BytesIO(data), filename=filename)
        else:
            upload_file = await attachment.to_file(filename=filename)

        new_message = None
        try:
            channel = await self.bot.fetch_channel(warehouse_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                raise ValueError("资源仓库频道类型无效。")
            new_message = await channel.send(file=upload_file)
            result = await session.execute(
                update(Resource).where(
                    Resource.id == resource_id,
                    Resource.thread_id == thread_id,
                    Resource.upload_mode == UploadMode.SECURE,
                    Resource.source_message_id == expected_source_message_id,
                ).values(
                    source_message_id=new_message.id,
                    filename=filename,
                ).execution_options(synchronize_session=False),
            )
            if result.rowcount != 1:
                raise ValueError("资源已被删除或换源，请刷新面板后重试。")
            await session.commit()
        except Exception:
            await session.rollback()
            if new_message is not None:
                try:
                    await new_message.delete()
                except Exception:
                    logger.exception("清理未提交的换源文件失败: %s", new_message.id)
            raise
        finally:
            upload_file.close()

        try:
            old_message = await channel.fetch_message(expected_source_message_id)
            await old_message.delete()
        except discord.NotFound:
            pass
        except Exception:
            logger.exception("换源成功，但旧仓库消息清理失败: %s", expected_source_message_id)
            return (
                "✅ 换源成功！原有设置和下载次数已保留。\n"
                "⚠️ 旧仓库文件清理失败，请联系管理员处理。"
            )
        return "✅ 换源成功！原有设置和下载次数已保留。"

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
