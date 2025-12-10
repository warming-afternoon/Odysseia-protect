# -*- coding: utf-8 -*-
"""
管理功能的 UI 组件 (View 和 Modal)
"""

import logging
from typing import Sequence, Optional, TYPE_CHECKING

import discord

from src.database.database import AsyncSessionLocal
from src.database.models import Resource, Thread, UploadMode

if TYPE_CHECKING:
    from src.services.management_service import ManagementService

logger = logging.getLogger(__name__)


class ManagementModal(discord.ui.Modal, title="编辑资源信息"):
    """一个用于编辑资源信息的弹出式模态框。"""

    def __init__(self, resource: Resource, service: "ManagementService"):
        super().__init__()
        self.resource = resource
        self.service = service

        self.version_info_input = discord.ui.TextInput(
            label="版本信息",
            default=self.resource.version_info,
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
        )
        self.password_input = discord.ui.TextInput(
            label="下载密码 (留空则无密码)",
            default=self.resource.password,
            style=discord.TextStyle.short,
            required=False,
            max_length=50,
        )

        self.add_item(self.version_info_input)
        self.add_item(self.password_input)

    async def on_submit(self, interaction: discord.Interaction):
        """当用户提交模态框时，调用服务层更新资源。"""
        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            try:
                updated = await self.service.update_resource(
                    session,
                    resource_id=self.resource.id,
                    version_info=self.version_info_input.value,
                    password=self.password_input.value or None,
                )
                if updated:
                    # 关键修复：提交数据库事务以保存更改
                    await session.commit()
                    await interaction.followup.send(
                        "✅ 资源信息已成功更新！", ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "❌ 更新失败，找不到该资源。", ephemeral=True
                    )
            except Exception as e:
                # 如果发生错误，回滚事务
                await session.rollback()
                logger.error(f"更新资源 {self.resource.id} 时发生错误", exc_info=e)
                await interaction.followup.send(
                    "❌ 更新过程中发生内部错误。", ephemeral=True
                )


class DeleteConfirmationView(discord.ui.View):
    """一个用于确认资源删除操作的视图。"""

    def __init__(
        self,
        resource: Resource,
        service: "ManagementService",
        original_interaction: discord.Interaction,
    ):
        super().__init__(timeout=180)  # 3分钟超时
        self.resource = resource
        self.service = service
        self.original_interaction = original_interaction

    @discord.ui.button(label="确认删除", style=discord.ButtonStyle.danger)
    async def confirm_delete(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """执行删除，然后刷新并返回管理面板。"""
        await interaction.response.defer()  # 立即响应交互

        async with AsyncSessionLocal() as session:
            try:
                success = await self.service.delete_resource(
                    session, resource_id=self.resource.id
                )
                if success:
                    await session.commit()
                    await interaction.followup.send(
                        "✅ 资源已成功删除。", ephemeral=True
                    )
                else:
                    await session.rollback()
                    await interaction.followup.send(
                        "❌ 删除失败，找不到该资源。", ephemeral=True
                    )
            except Exception as e:
                await session.rollback()
                logger.error(f"删除资源 {self.resource.id} 时发生错误", exc_info=e)
                await interaction.followup.send(
                    "❌ 删除过程中发生内部错误。", ephemeral=True
                )

            # 无论成功失败，都刷新管理面板
            refreshed_panel = await self.service.handle_management_request(
                session, interaction=self.original_interaction
            )
            await self.original_interaction.edit_original_response(**refreshed_panel)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel_delete(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """取消删除并返回管理面板。"""
        await interaction.response.defer()

        async with AsyncSessionLocal() as session:
            refreshed_panel = await self.service.handle_management_request(
                session, interaction=self.original_interaction
            )
            await self.original_interaction.edit_original_response(**refreshed_panel)


class ManagementView(discord.ui.View):
    """管理资源的交互式视图，包含选择、编辑和删除功能。"""

    def __init__(
        self,
        resources: Sequence[Resource],
        service: "ManagementService",
        original_interaction: discord.Interaction,
        thread: "Thread",
    ):
        super().__init__(timeout=300)  # 5分钟后超时
        self.resources = {r.id: r for r in resources}
        self.service = service
        self.original_interaction = original_interaction
        self.thread = thread
        self.selected_resource_id: Optional[int] = None

        # 初始化并添加组件
        self.select_menu = self.ResourceManagementSelect(resources)
        self.edit_button = self.EditButton()
        self.delete_button = self.DeleteButton()
        self.toggle_reaction_button = self.ToggleReactionWallButton(thread)
        self.set_reaction_emoji_button = self.SetReactionEmojiButton(thread)

        self.add_item(self.select_menu)
        self.add_item(self.edit_button)
        self.add_item(self.delete_button)
        self.add_item(self.toggle_reaction_button)
        self.add_item(self.set_reaction_emoji_button)

    async def on_timeout(self):
        """超时后禁用所有组件。"""
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        try:
            await self.original_interaction.edit_original_response(view=self)
        except discord.NotFound:
            pass  # 消息可能已被删除

    class ResourceManagementSelect(discord.ui.Select):
        def __init__(self, resources: Sequence[Resource]):
            options = []
            for r in resources[:25]:
                mode_icon = "🔒" if r.upload_mode == UploadMode.SECURE else "📄"
                options.append(
                    discord.SelectOption(
                        label=f"{mode_icon} 版本: {r.version_info or '未命名'}",
                        description=f"文件名: {r.filename or 'N/A'}",
                        value=str(r.id),
                    )
                )
            super().__init__(placeholder="请选择要操作的资源...", options=options)

        async def callback(self, interaction: discord.Interaction):
            if not isinstance(self.view, ManagementView):
                return
            view = self.view
            view.selected_resource_id = int(self.values[0])
            view.edit_button.disabled = False
            view.delete_button.disabled = False
            await interaction.response.edit_message(view=view)

    class EditButton(discord.ui.Button):
        def __init__(self):
            super().__init__(
                label="编辑", style=discord.ButtonStyle.primary, disabled=True
            )

        async def callback(self, interaction: discord.Interaction):
            if not isinstance(self.view, ManagementView):
                return
            view = self.view
            if view.selected_resource_id is not None:
                resource = view.resources.get(view.selected_resource_id)
                if resource:
                    modal = ManagementModal(resource, view.service)
                    await interaction.response.send_modal(modal)

    class DeleteButton(discord.ui.Button):
        def __init__(self):
            super().__init__(
                label="删除", style=discord.ButtonStyle.danger, disabled=True
            )

        async def callback(self, interaction: discord.Interaction):
            """当点击删除按钮时，显示一个二次确认界面。"""
            if not isinstance(self.view, ManagementView):
                return

            view = self.view
            if view.selected_resource_id is not None:
                resource = view.resources.get(view.selected_resource_id)
                if resource:
                    # 1. 创建确认界面的 Embed
                    confirmation_embed = discord.Embed(
                        title="⚠️ 删除确认",
                        description=f"您确定要删除以下资源吗？此操作不可逆！\n\n"
                        f"**文件名**: `{resource.filename}`\n"
                        f"**版本信息**: `{resource.version_info}`",
                        color=discord.Color.orange(),
                    )
                    # 2. 创建包含“确认”和“取消”按钮的视图
                    confirmation_view = DeleteConfirmationView(
                        resource, view.service, view.original_interaction
                    )
                    # 3. 编辑原消息，显示确认界面
                    await interaction.response.edit_message(
                        embed=confirmation_embed, view=confirmation_view
                    )

    class ToggleReactionWallButton(discord.ui.Button):
        def __init__(self, thread: "Thread"):
            # 根据当前状态设置按钮的标签和样式
            is_enabled = thread.reaction_required
            super().__init__(
                label="关闭反应墙" if is_enabled else "开启反应墙",
                style=(
                    discord.ButtonStyle.danger
                    if is_enabled
                    else discord.ButtonStyle.success
                ),
                row=2,  # 放在新的一行
            )

        async def callback(self, interaction: discord.Interaction):
            if not isinstance(self.view, ManagementView):
                return

            view = self.view
            service = view.service
            thread_to_update = view.thread
            original_interaction = view.original_interaction

            await interaction.response.defer()

            async with AsyncSessionLocal() as session:
                try:
                    # 获取最新的帖子状态以防万一
                    fresh_thread = await service.thread_repo.get(
                        session, id=thread_to_update.id
                    )
                    if not fresh_thread:
                        await interaction.followup.send(
                            "❌ 错误：找不到帖子。", ephemeral=True
                        )
                        return

                    # 切换状态并更新
                    new_status = not fresh_thread.reaction_required
                    update_data = {"reaction_required": new_status}
                    await service.thread_repo.update(
                        session,
                        db_obj=fresh_thread,
                        obj_in=update_data,
                    )
                    await session.commit()

                    # 刷新整个管理面板
                    refreshed_panel = await service.handle_management_request(
                        session, interaction=original_interaction
                    )
                    await original_interaction.edit_original_response(**refreshed_panel)

                except Exception as e:
                    await session.rollback()
                    logger.error(
                        f"切换反应墙状态时出错，帖子ID: {thread_to_update.id}",
                        exc_info=e,
                    )
                    await interaction.followup.send(
                        "❌ 切换状态时发生内部错误。", ephemeral=True
                    )

    class SetReactionEmojiButton(discord.ui.Button):
        def __init__(self, thread: "Thread"):
            # 按钮标签和样式
            super().__init__(
                label="设置反应",
                style=discord.ButtonStyle.secondary,
                row=2,  # 与切换按钮同一行
                emoji="😀",
            )
            self.thread = thread

        async def callback(self, interaction: discord.Interaction):
            if not isinstance(self.view, ManagementView):
                return

            view = self.view
            # 弹出模态框
            modal = SetReactionEmojiModal(view.service, self.thread)
            await interaction.response.send_modal(modal)


class SetReactionEmojiModal(discord.ui.Modal, title="设置反应表情"):
    """用于设置自定义反应表情的模态框。"""

    def __init__(self, service: "ManagementService", thread: "Thread"):
        super().__init__()
        self.service = service
        self.thread = thread

        self.emoji_input = discord.ui.TextInput(
            label="反应表情",
            placeholder="输入一个emoji，例如: 👍, 🔥, 🎉 (留空则清除)",
            default=thread.reaction_emoji or "",
            style=discord.TextStyle.short,
            required=False,
            max_length=50,
        )
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        emoji = self.emoji_input.value.strip()
        # 验证：如果非空，确保是单个有效的emoji（简单检查）
        if emoji and len(emoji) > 10:  # 粗略检查，实际可以更严格
            await interaction.followup.send(
                "❌ 请输入一个有效的emoji（长度不超过10个字符）。", ephemeral=True
            )
            return

        async with AsyncSessionLocal() as session:
            try:
                # 获取最新的帖子状态
                fresh_thread = await self.service.thread_repo.get(
                    session, id=self.thread.id
                )
                if not fresh_thread:
                    await interaction.followup.send(
                        "❌ 错误：找不到帖子。", ephemeral=True
                    )
                    return

                update_data = {"reaction_emoji": emoji if emoji else None}
                await self.service.thread_repo.update(
                    session,
                    db_obj=fresh_thread,
                    obj_in=update_data,
                )
                await session.commit()

                # 刷新整个管理面板
                refreshed_panel = await self.service.handle_management_request(
                    session, interaction=interaction
                )
                await interaction.edit_original_response(**refreshed_panel)

            except Exception as e:
                await session.rollback()
                logger.error(
                    f"设置反应表情时出错，帖子ID: {self.thread.id}",
                    exc_info=e,
                )
                await interaction.followup.send(
                    "❌ 设置反应表情时发生内部错误。", ephemeral=True
                )
