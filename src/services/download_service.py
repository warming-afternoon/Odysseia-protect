# -*- coding: utf-8 -*-
"""
下载服务，负责处理文件下载相关的业务逻辑。
"""

import logging
from typing import Any, Optional

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import UploadMode
from src.services.base import BaseService
from src.ui.download_ui import ResourceSelectView
from src.utils.formatting import format_resource_list

logger = logging.getLogger(__name__)


class DownloadService(BaseService):
    """封装了所有与资源下载相关的业务逻辑。"""

    async def handle_download_request(
        self, session: AsyncSession, *, interaction: discord.Interaction
    ) -> dict[str, Any]:
        """处理 /下载 命令的请求，返回包含 Embed 和 View 的字典。"""
        if not interaction.channel or not isinstance(
            interaction.channel, (discord.TextChannel, discord.Thread)
        ):
            embed = discord.Embed(
                title="❌ 操作无效",
                description="此命令只能在服务器的文本频道或帖子中使用。",
                color=discord.Color.red(),
            )
            return {"embed": embed}

        thread_model = await self.thread_repo.get_by_public_thread_id(
            session, public_thread_id=interaction.channel.id
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

        # --- 新逻辑：按模式分组资源 ---
        secure_resources = [r for r in resources if r.upload_mode == UploadMode.SECURE]
        normal_resources = [r for r in resources if r.upload_mode == UploadMode.NORMAL]

        embed = discord.Embed(
            title="📄 版本选择",
            description="资源已按模式分类。请从下面的下拉菜单中选择一项进行下载。",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="🔒 受保护资源",
            value=format_resource_list(secure_resources, interaction=interaction),
            inline=False,
        )
        embed.add_field(
            name="📄 资源",
            value=format_resource_list(
                normal_resources, is_normal_mode=True, interaction=interaction
            ),
            inline=False,
        )

        # 关键修改：只将受保护的资源传递给下拉菜单视图
        view = ResourceSelectView(secure_resources)
        return {"embed": embed, "view": view}

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
