# -*- coding: utf-8 -*-
"""
下载功能的 UI 组件 (View 和 Modal)
"""

import logging
from collections.abc import Callable
from typing import Sequence

import discord

from src.database.database import AsyncSessionLocal
from src.database.models import Resource, UploadMode
from src.database.repositories.resource import ResourceRepository
from src.ui.password_input_modal import DownloadResponseMode, PasswordModal
from src.dto.resource_dto import ResourceDTO

logger = logging.getLogger(__name__)

class ResourceSelect(discord.ui.Select):
    """
    资源选择下拉菜单。
    """

    def __init__(
        self,
        resources: Sequence[Resource],
        *,
        resource_list_embed: discord.Embed | None = None,
        response_mode: DownloadResponseMode = DownloadResponseMode.EDIT_PRIVATE_PANEL,
        private_view_factory: Callable[[], discord.ui.View] | None = None,
    ):
        self.resource_list_embed = resource_list_embed
        self.response_mode = response_mode
        self.private_view_factory = private_view_factory
        options = []
        # Discord 的下拉菜单最多只能有 25 个选项
        for resource in resources[:25]:
            mode_icon = "🔒" if resource.upload_mode == UploadMode.SECURE else "📄"
            
            # 构建 label 和 description，确保不超过 Discord 的 100 字符限制
            label_text = f"{mode_icon} 版本: {resource.version_info or '未命名'}"
            if len(label_text) > 100:
                label_text = label_text[:90] + "..."
            
            desc_text = f"文件名: {resource.filename or 'N/A'}"
            if len(desc_text) > 100:
                desc_text = desc_text[:90] + "..."
            
            # 为每个资源创建一个选项
            option = discord.SelectOption(
                label=label_text,
                description=desc_text,
                value=str(resource.id),
            )
            options.append(option)

        # 如果没有可用的选项，创建一个禁用的占位符
        if not options:
            options.append(
                discord.SelectOption(
                    label="没有找到任何资源", value="disabled", default=True
                )
            )

        super().__init__(
            placeholder="请选择一个资源版本进行下载...",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not options or options[0].value == "disabled",
        )

    async def callback(self, interaction: discord.Interaction):
        """
        当用户在下拉菜单中做出选择时，此回调被触发。
        它会根据资源是否加密来决定下一步操作：
        - 如果有密码，则弹出密码输入模态框。
        - 如果没有密码，则延迟响应，获取链接，然后发送结果。
        """
        selected_resource_id = int(self.values[0])
        if self.view is not None and hasattr(
            self.view, "clear_authorized_selection"
        ):
            self.view.clear_authorized_selection()

        async with AsyncSessionLocal() as session:
            resource_repo = ResourceRepository()
            # 预加载关联的 Thread 对象
            selected_resource = await resource_repo.get_with_thread(
                session, id=selected_resource_id
            )

        if not selected_resource:
            await interaction.response.send_message(
                "错误：找不到所选的资源，它可能已被删除。", ephemeral=True
            )
            return

        # 将 ORM 对象转换为 DTO，避免 DetachedInstanceError
        resource_dto = ResourceDTO(
            id=selected_resource.id,
            filename=selected_resource.filename,
            version_info=selected_resource.version_info,
            password=selected_resource.password,
            source_message_id=selected_resource.source_message_id,
            warehouse_thread_id=selected_resource.thread.warehouse_thread_id,
            public_thread_id=selected_resource.thread.public_thread_id,
            author_id=selected_resource.thread.author_id,
            guild_id=selected_resource.thread.guild_id,
            public_thread_name=selected_resource.thread.public_thread_name,
            source_status=selected_resource.thread.source_status,
            upload_mode=selected_resource.upload_mode,
        )

        resource_list_embed = self.resource_list_embed
        if (
            resource_list_embed is None
            and interaction.message
            and interaction.message.embeds
        ):
            resource_list_embed = interaction.message.embeds[-1]

        if resource_list_embed is None or self.view is None:
            await interaction.response.send_message(
                "❌ 下载面板状态已失效，请重新使用 `/下载` 或右键“打开下载面板”。",
                ephemeral=True,
            )
            return

        panel_view = self.view
        if self.response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL:
            if self.private_view_factory is None:
                await interaction.response.send_message(
                    "❌ 下载面板状态已失效，请重新发送“下载”。",
                    ephemeral=True,
                )
                return
            panel_view = self.private_view_factory()

        # 如果资源有密码，立即弹出模态框
        if resource_dto.password:
            modal = PasswordModal(
                resource=resource_dto,
                resource_list_embed=resource_list_embed,
                panel_view=panel_view,
                response_mode=self.response_mode,
            )
            await interaction.response.send_modal(modal)
            if (
                self.response_mode is DownloadResponseMode.EDIT_PRIVATE_PANEL
                and interaction.message
            ):
                try:
                    await interaction.message.edit(view=self.view)
                except discord.HTTPException:
                    logger.warning("无法在密码验证前刷新心愿单按钮状态")
            return

        # 对于没有密码的资源
        if self.response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL:
            await interaction.response.defer(ephemeral=True, thinking=True)
        else:
            await interaction.response.defer()

        try:
            download_service = getattr(interaction.client, "download_service", None)
            if download_service is None:
                raise RuntimeError("Bot 未配置下载服务。")
            fresh_url = await download_service.fetch_fresh_url(resource_dto)

            # 触发下载事件，供其他组件监听（如下载计数器）
            interaction.client.dispatch("resource_downloaded", resource_dto)

            response_embed = download_service.build_download_embed(
                resource_dto, fresh_url
            )
            if hasattr(panel_view, "authorize_selection"):
                await panel_view.authorize_selection(
                    interaction,
                    resource_id=selected_resource_id,
                )
            await interaction.edit_original_response(
                embeds=[response_embed, resource_list_embed],
                view=panel_view,
            )

        except Exception as e:
            logger.error(
                f"为资源 {selected_resource_id} 获取新下载链接失败", exc_info=e
            )
            error_message = (
                "❌ 抱歉，获取下载链接时发生错误。"
                "源文件可能已被删除或Bot无法访问。"
            )
            if self.response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL:
                await interaction.edit_original_response(
                    content=error_message,
                    embeds=[],
                    view=None,
                )
            else:
                await interaction.followup.send(error_message, ephemeral=True)
