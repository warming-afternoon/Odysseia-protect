# -*- coding: utf-8 -*-
"""
下载服务，负责处理文件下载相关的业务逻辑。
"""

import logging
from typing import Any, Optional, Union

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import UploadMode
from src.dto.resource_dto import ResourceDTO
from src.services.base import BaseService
from src.ui.download_entry_ui import DownloadEntryView, build_download_entry_embed
from src.ui.resource_select_view import ResourceSelectView
from src.utils.formatting import format_resource_list_chunks

logger = logging.getLogger(__name__)


class DownloadService(BaseService):
    """封装了所有与资源下载相关的业务逻辑。"""

    async def handle_download_request(
        self,
        session: AsyncSession,
        *,
        source: Union[discord.Interaction, discord.Message],
    ) -> dict[str, Any]:
        """处理 /下载 命令的请求，返回包含 Embed 和 View 的字典。"""
        if not source.channel or not isinstance(
            source.channel, (discord.TextChannel, discord.Thread)
        ):
            embed = discord.Embed(
                title="❌ 操作无效",
                description="此命令只能在服务器的文本频道或帖子中使用。",
                color=discord.Color.red(),
            )
            return {"embed": embed}

        thread_model = await self.thread_repo.get_by_public_thread_id(
            session, public_thread_id=source.channel.id
        )

        if not thread_model:
            embed = discord.Embed(
                title="📂 暂无资源",
                description="这个帖子还没有上传任何文件。使用 `/上传` 命令来添加第一个文件吧！",
                color=discord.Color.blue(),
            )
            return {"embed": embed}

        resources = await self.resource_repo.get_by_thread_id(
            session, thread_id=thread_model.id
        )

        if not resources:
            embed = discord.Embed(
                title="📂 暂无资源",
                description="这个帖子还没有上传任何文件。使用 `/上传` 命令来添加第一个文件吧！",
                color=discord.Color.blue(),
            )
            return {"embed": embed}

        # 按模式分组资源
        secure_resources = [r for r in resources if r.upload_mode == UploadMode.SECURE]
        normal_resources = [r for r in resources if r.upload_mode == UploadMode.NORMAL]

        embed = discord.Embed(
            title="📄 版本选择",
            description="资源已按模式分类。请从下面的下拉菜单中选择一项进行下载。",
            color=discord.Color.green(),
        )
        # 按分页块添加受保护资源
        secure_chunks = format_resource_list_chunks(secure_resources, source=source, show_download_count=False)
        for i, chunk in enumerate(secure_chunks):
            name = "🔒 受保护资源" if i == 0 else "🔒 受保护资源 (续)"
            embed.add_field(name=name, value=chunk, inline=False)

        # 按分页块添加普通资源
        normal_chunks = format_resource_list_chunks(normal_resources, is_normal_mode=True, source=source)
        for i, chunk in enumerate(normal_chunks):
            name = "📄 资源" if i == 0 else "📄 资源 (续)"
            embed.add_field(name=name, value=chunk, inline=False)

        # 只将受保护的资源传递给下拉菜单视图
        view = ResourceSelectView(
            secure_resources,
            resource_list_embed=embed,
        )
        return {"embed": embed, "view": view}

    async def ensure_download_entry(
        self,
        session: AsyncSession,
        *,
        channel: discord.Thread,
        thread_model,
    ) -> None:
        """幂等创建帖子内的公开下载入口消息。"""
        if thread_model.download_panel_message_id:
            try:
                await channel.fetch_message(thread_model.download_panel_message_id)
                return
            except discord.NotFound:
                logger.warning(
                    "帖子 %s 的下载入口消息已不存在，将重新创建。", channel.id
                )
            except discord.Forbidden:
                logger.warning("Bot 无权检查帖子 %s 的下载入口消息。", channel.id)
                return

        message = await channel.send(
            embed=build_download_entry_embed(), view=DownloadEntryView()
        )
        await self.thread_repo.update(
            session,
            db_obj=thread_model,
            obj_in={"download_panel_message_id": message.id},
        )

        try:
            await message.pin(reason="固定 Odysseia Protect 下载入口")
        except discord.Forbidden:
            logger.warning("Bot 无权置顶帖子 %s 的下载入口消息。", channel.id)
        except discord.HTTPException as exc:
            logger.warning("置顶帖子 %s 的下载入口消息失败: %s", channel.id, exc)

    async def create_download_view(
        self, session: AsyncSession, *, public_thread_id: int
    ) -> tuple[Optional[discord.ui.View], str]:
        """
        Creates a view with a dropdown for users to select a resource to download.

        This method is designed for testing and direct view creation,
        contrasting with handle_download_request which returns a full dict payload.
        """
        thread_model = await self.thread_repo.get_by_public_thread_id(
            session, public_thread_id=public_thread_id
        )

        if not thread_model:
            return None, "此帖还没有任何资源。"

        resources = await self.resource_repo.get_multi_by_thread_id(
            session, thread_id=thread_model.id
        )

        if not resources:
            return None, "此帖还没有任何资源。"

        view = ResourceSelectView(resources)
        return view, "请选择你要下载的版本："

    async def increment_download_count(self, session: AsyncSession, resource_id: int):
        """
        为给定资源增加下载计数
        """
        db_resource = await self.resource_repo.get(session, id=resource_id)

        # 如果找不到资源，则记录警告并提前返回
        if not db_resource:
            logger.warning(
                f"尝试为资源 ID {resource_id} 增加下载计数，但在数据库中未找到该资源。"
            )
            return

        db_resource.download_count += 1
        session.add(db_resource)  # 将更改暂存，由调用方的上下文管理器负责提交。
        logger.info(
            f"资源 {db_resource.id} 的下载计数已增加至 {db_resource.download_count}"
        )

    async def fetch_fresh_url(self, resource: ResourceDTO) -> str:
        """根据源消息动态获取当前有效的 Discord 附件 URL。"""
        channel_id = resource.warehouse_thread_id or resource.public_thread_id
        if not channel_id:
            raise ValueError("数据库中未找到该资源关联的频道ID。")

        source_channel = await self.bot.fetch_channel(channel_id)
        if not isinstance(source_channel, (discord.TextChannel, discord.Thread)):
            raise ValueError("资源源频道类型无效。")

        source_message = await source_channel.fetch_message(resource.source_message_id)
        if not source_message.attachments:
            raise ValueError("源消息中没有附件。")
        return source_message.attachments[0].url

    @staticmethod
    def build_download_embed(resource: ResourceDTO, fresh_url: str) -> discord.Embed:
        """构建同时适合直接下载和复制到 SillyTavern 的结果页。"""
        embed = discord.Embed(
            title="📥 角色卡下载",
            description=(
                f"**版本：** {resource.version_info}\n"
                f"**文件：** `{resource.filename or '未命名文件'}`\n\n"
                "📋 **SillyTavern 快速导入 URL**\n"
                f"`{fresh_url}`\n\n"
                f"[🌐 打开下载链接]({fresh_url})\n\n"
                "链接具有时效性；失效后请重新打开下载面板获取。"
            ),
            color=discord.Color.green(),
        )
        filename = (resource.filename or "").lower()
        if filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            embed.set_image(url=fresh_url)
        return embed
