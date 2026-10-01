# -*- coding: utf-8 -*-
"""
下载功能的 UI 组件 (View 和 Modal)
"""

import logging
from collections.abc import Callable
from typing import Sequence

import discord

from src.database.database import AsyncSessionLocal
from src.dto.download_resource_option_dto import DownloadResourceOptionDTO
from src.enums import UploadMode
from src.qo.resource_selection_qo import ResourceSelectionQo
from src.ui.download_embed_builder import DownloadEmbedBuilder
from src.enums.download_response_mode import DownloadResponseMode
from src.ui.password_modal import PasswordModal
from src.ui.trace_consent_view import send_trace_consent

logger = logging.getLogger(__name__)

class ResourceSelect(discord.ui.Select):
    """当前资源页的版本选择下拉菜单。"""

    def __init__(
        self,
        resources: Sequence[DownloadResourceOptionDTO],
        *,
        public_thread_id: int,
        resource_list_embed: discord.Embed | None = None,
        response_mode: DownloadResponseMode = DownloadResponseMode.EDIT_PRIVATE_PANEL,
        private_view_factory: Callable[..., discord.ui.View] | None = None,
    ):
        """根据服务提供的当前页数据构建资源选项。"""
        self.public_thread_id = public_thread_id
        self.resource_list_embed = resource_list_embed
        self.response_mode = response_mode
        self.private_view_factory = private_view_factory
        options = []
        # 服务已完成数据库分页，菜单仅展示本页资源。
        for resource in resources:
            if getattr(resource, "trace_enabled", False):
                mode_icon = "🔎"
            else:
                mode_icon = "🔒" if resource.upload_mode == UploadMode.SECURE else "📄"

            # 构建 label 和 description，确保不超过 Discord 的 100 字符限制
            label_text = f"{mode_icon} 版本: {resource.version_info or '未命名'}"
            if len(label_text) > 100:
                label_text = label_text[:90] + "..."

            upload_date = resource.created_at.strftime("%Y/%m/%d")
            desc_prefix = f"{upload_date} · 文件名: "
            filename = resource.filename or "N/A"
            max_filename_length = 100 - len(desc_prefix)
            if len(filename) > max_filename_length:
                filename = filename[: max_filename_length - 3] + "..."
            desc_text = desc_prefix + filename

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
            row=0,
            min_values=1,
            max_values=1,
            options=options,
            disabled=not options or options[0].value == "disabled",
        )

    async def callback(self, interaction: discord.Interaction):
        """串行处理版本选择，防止与同一面板的翻页冲突。"""
        if self.view is None:
            await interaction.response.send_message(
                "❌ 下载面板状态已失效，请重新使用 `/下载` 或右键“打开下载面板”。",
                ephemeral=True,
            )
            return
        if self.view.state_lock.locked():
            await interaction.response.send_message("ℹ️ 面板正在更新，请稍后重试。", ephemeral=True)
            return
        async with self.view.state_lock:
            await self._handle_selection(interaction)

    async def _handle_selection(self, interaction: discord.Interaction):
        """通过服务重新取得资源详情并进入对应下载流程。"""
        # 拒绝已经翻页的旧菜单，避免恢复过期资源列表。
        if self not in self.view.children or self.view.is_finished():
            await interaction.response.send_message(
                "❌ 下载面板状态已失效，请重新使用 `/下载` 或右键“打开下载面板”。",
                ephemeral=True,
            )
            return
        selected_resource_id = int(self.values[0])
        if selected_resource_id not in {int(option.value) for option in self.options if option.value != "disabled"}:
            await interaction.response.send_message("❌ 所选资源不在当前页。", ephemeral=True)
            return
        download_service = interaction.client.download_service
        # ORM 详情在服务内部转换，回调仅接收独立的数据对象。
        try:
            async with AsyncSessionLocal() as session:
                resource_dto = await download_service.get_selected_resource(
                    session, ResourceSelectionQo(
                        public_thread_id=self.public_thread_id, resource_id=selected_resource_id,
                    ),
                )
        except Exception:
            logger.exception("读取下载资源失败")
            await interaction.response.send_message("❌ 读取资源失败，请稍后重试。", ephemeral=True)
            return
        if resource_dto is None:
            await interaction.response.send_message(
                "错误：找不到所选的资源，它可能已被删除。", ephemeral=True,
            )
            return
        if hasattr(self.view, "clear_authorized_selection"):
            self.view.clear_authorized_selection()

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
            panel_view = self.private_view_factory(user_id=interaction.user.id)

        # 如果资源有密码，立即弹出模态框
        if resource_dto.password:
            modal = PasswordModal(
                resource=resource_dto,
                resource_list_embed=resource_list_embed,
                panel_view=panel_view,
                response_mode=self.response_mode,
            )
            await interaction.response.send_modal(modal)
            return

        if resource_dto.trace_enabled:
            await send_trace_consent(
                interaction,
                resource=resource_dto,
                resource_list_embed=resource_list_embed,
                panel_view=panel_view,
            )
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

            response_embed = DownloadEmbedBuilder.build_download_embed(
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
                attachments=[],
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
