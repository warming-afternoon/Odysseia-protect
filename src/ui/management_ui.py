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

    def __init__(
        self,
        resource: Resource,
        service: "ManagementService",
        management_view: "ManagementView | None" = None,
    ):
        super().__init__()
        self.resource = resource
        self.service = service
        self.management_view = management_view

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

        if self.management_view is not None:
            await self.management_view.refresh(interaction)


class ReplaceSourceModal(discord.ui.Modal, title="换源"):
    def __init__(self, resource: Resource, view: "ManagementView"):
        super().__init__(timeout=300)
        self.management_view = view
        self.resource_id = resource.id
        self.expected_source_message_id = resource.source_message_id
        self.file_input = discord.ui.FileUpload(
            min_values=1, max_values=1, required=True
        )
        self.add_item(
            discord.ui.Label(
                text=f"版本：{resource.version_info}"[:45],
                description=f"原文件：{resource.filename}"[:100],
                component=self.file_input,
            )
        )
        self.add_item(discord.ui.TextDisplay(
            "提交即替换此版本的文件，保留版本信息、密码、下载次数和保护设置。"
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            if len(self.file_input.values) != 1:
                raise ValueError("请上传且仅上传一个文件。")
            async with AsyncSessionLocal() as session:
                result = await self.management_view.service.replace_resource_source(
                    session, resource_id=self.resource_id, interaction=interaction,
                    attachment=self.file_input.values[0],
                    expected_source_message_id=self.expected_source_message_id,
                )
        except ValueError as exc:
            result = f"❌ {exc}"
        except Exception:
            logger.exception("资源 %s 换源失败", self.resource_id)
            result = "❌ 换源失败，原资源未替换，请稍后重试。"
        await interaction.followup.send(result, ephemeral=True)
        await self.management_view.refresh(interaction)


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
                session, interaction=self.original_interaction,
                selected_resource_id=self.resource.id,
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
                session, interaction=self.original_interaction,
                selected_resource_id=self.resource.id,
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
        selected_resource_id: Optional[int] = None,
    ):
        super().__init__(timeout=300)  # 5分钟后超时
        self.resources = {r.id: r for r in resources}
        self.service = service
        self.original_interaction = original_interaction
        self.thread = thread
        self.selected_resource_id: Optional[int] = None

        # 只有在有资源的情况下才添加资源管理组件
        if self.resources:
            self.select_menu = self.ResourceManagementSelect(resources)
            self.edit_button = self.EditButton()
            self.replace_button = self.ReplaceButton()
            self.delete_button = self.DeleteButton()
            self.add_item(self.select_menu)
            self.add_item(self.edit_button)
            self.add_item(self.replace_button)
            self.add_item(self.delete_button)
            self.set_selection(selected_resource_id)

        # # 总是添加反应墙管理组件
        # self.toggle_reaction_button = self.ToggleReactionWallButton(thread)
        # self.set_reaction_emoji_button = self.SetReactionEmojiButton(thread)
        # self.add_item(self.toggle_reaction_button)
        # self.add_item(self.set_reaction_emoji_button)

        # 添加快捷模式按钮
        self.toggle_quick_mode_button = self.ToggleQuickModeButton(thread)
        self.add_item(self.toggle_quick_mode_button)

    def set_selection(self, resource_id: Optional[int]):
        resource = self.resources.get(resource_id)
        self.selected_resource_id = resource.id if resource else None
        for option in self.select_menu.options:
            option.default = option.value == str(self.selected_resource_id)
        self.edit_button.disabled = resource is None
        self.delete_button.disabled = resource is None
        self.replace_button.disabled = (
            resource is None or resource.upload_mode != UploadMode.SECURE
        )

    async def refresh(self, interaction: discord.Interaction):
        try:
            async with AsyncSessionLocal() as session:
                panel = await self.service.handle_management_request(
                    session, interaction=self.original_interaction,
                    selected_resource_id=self.selected_resource_id,
                )
            await self.original_interaction.edit_original_response(**panel)
            self.stop()
        except Exception:
            logger.exception("刷新管理面板失败")
            await interaction.followup.send(
                "管理面板刷新失败，请重新使用 `/管理` 查看最新状态。", ephemeral=True,
            )

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
                
                # 构建 label 和 description，确保不超过 Discord 的 100 字符限制
                label_text = f"{mode_icon} 版本: {r.version_info or '未命名'}"
                if len(label_text) > 100:
                    label_text = label_text[:90] + "..."
                
                desc_text = f"文件名: {r.filename or 'N/A'}"
                if len(desc_text) > 100:
                    desc_text = desc_text[:90] + "..."
                
                options.append(
                    discord.SelectOption(
                        label=label_text,
                        description=desc_text,
                        value=str(r.id),
                    )
                )
            super().__init__(
                placeholder="请选择要操作的资源...",
                options=options,
                disabled=not options,  # 如果没有选项，禁用菜单
                row=0,
            )

        async def callback(self, interaction: discord.Interaction):
            if not isinstance(self.view, ManagementView):
                return
            view = self.view
            view.set_selection(int(self.values[0]))
            await interaction.response.edit_message(view=view)

    class EditButton(discord.ui.Button):
        def __init__(self):
            super().__init__(
                label="编辑", style=discord.ButtonStyle.primary, disabled=True, row=1,
            )

        async def callback(self, interaction: discord.Interaction):
            if not isinstance(self.view, ManagementView):
                return
            view = self.view
            if view.selected_resource_id is not None:
                resource = view.resources.get(view.selected_resource_id)
                if resource:
                    modal = ManagementModal(resource, view.service, view)
                    await interaction.response.send_modal(modal)

    class ReplaceButton(discord.ui.Button):
        def __init__(self):
            super().__init__(
                label="换源",
                style=discord.ButtonStyle.primary,
                disabled=True,
                row=1,
            )

        async def callback(self, interaction: discord.Interaction):
            if not isinstance(self.view, ManagementView):
                return
            resource = self.view.resources.get(self.view.selected_resource_id)
            if resource is not None and resource.upload_mode == UploadMode.SECURE:
                await interaction.response.send_modal(
                    ReplaceSourceModal(resource, self.view)
                )

    class DeleteButton(discord.ui.Button):
        def __init__(self):
            super().__init__(
                label="删除", style=discord.ButtonStyle.danger, disabled=True, row=1,
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

    # class ToggleReactionWallButton(discord.ui.Button):
    #     def __init__(self, thread: "Thread"):
    #         # 根据当前状态设置按钮的标签和样式
    #         is_enabled = thread.reaction_required
    #         super().__init__(
    #             label="关闭反应墙" if is_enabled else "开启反应墙",
    #             style=(
    #                 discord.ButtonStyle.danger
    #                 if is_enabled
    #                 else discord.ButtonStyle.success
    #             ),
    #             row=2,  # 放在新的一行
    #         )
    #
    #     async def callback(self, interaction: discord.Interaction):
    #         if not isinstance(self.view, ManagementView):
    #             return
    #
    #         view = self.view
    #         service = view.service
    #         thread_to_update = view.thread
    #         original_interaction = view.original_interaction
    #
    #         await interaction.response.defer()
    #
    #         async with AsyncSessionLocal() as session:
    #             try:
    #                 # 获取最新的帖子状态以防万一
    #                 fresh_thread = await service.thread_repo.get(
    #                     session, id=thread_to_update.id
    #                 )
    #                 if not fresh_thread:
    #                     await interaction.followup.send(
    #                         "❌ 错误：找不到帖子。", ephemeral=True
    #                     )
    #                     return
    #
    #                 # 切换状态并更新
    #                 new_status = not fresh_thread.reaction_required
    #                 update_data = {"reaction_required": new_status}
    #                 await service.thread_repo.update(
    #                     session,
    #                     db_obj=fresh_thread,
    #                     obj_in=update_data,
    #                 )
    #                 await session.commit()
    #
    #                 # 刷新整个管理面板
    #                 refreshed_panel = await service.handle_management_request(
    #                     session, interaction=original_interaction
    #                 )
    #                 await original_interaction.edit_original_response(**refreshed_panel)
    #
    #             except Exception as e:
    #                 await session.rollback()
    #                 logger.error(
    #                     f"切换反应墙状态时出错，帖子ID: {thread_to_update.id}",
    #                     exc_info=e,
    #                 )
    #                 await interaction.followup.send(
    #                     "❌ 切换状态时发生内部错误。", ephemeral=True
    #                 )
    #
    # class SetReactionEmojiButton(discord.ui.Button):
    #     def __init__(self, thread: "Thread"):
    #         # 按钮标签和样式
    #         super().__init__(
    #             label="设置反应",
    #             style=discord.ButtonStyle.secondary,
    #             row=2,  # 与切换按钮同一行
    #             emoji="😀",
    #         )
    #         self.thread = thread
    #
    #     async def callback(self, interaction: discord.Interaction):
    #         if not isinstance(self.view, ManagementView):
    #             return
    #
    #         view = self.view
    #         # 弹出模态框
    #         modal = SetReactionEmojiModal(view.service, self.thread)
    #         await interaction.response.send_modal(modal)

    class ToggleQuickModeButton(discord.ui.Button):
        def __init__(self, thread: "Thread"):
            is_enabled = thread.quick_mode_enabled
            super().__init__(
                label="关闭快捷模式" if is_enabled else "开启快捷模式",
                style=(
                    discord.ButtonStyle.danger
                    if is_enabled
                    else discord.ButtonStyle.success
                ),
                row=3,
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
                    fresh_thread = await service.thread_repo.get(
                        session, id=thread_to_update.id
                    )
                    if not fresh_thread:
                        await interaction.followup.send(
                            "❌ 错误：找不到帖子。", ephemeral=True
                        )
                        return

                    new_status = not fresh_thread.quick_mode_enabled
                    update_data = {"quick_mode_enabled": new_status}
                    await service.thread_repo.update(
                        session,
                        db_obj=fresh_thread,
                        obj_in=update_data,
                    )
                    await session.commit()

                    refreshed_panel = await service.handle_management_request(
                        session, interaction=original_interaction,
                        selected_resource_id=view.selected_resource_id,
                    )
                    await original_interaction.edit_original_response(**refreshed_panel)

                except Exception as e:
                    await session.rollback()
                    logger.error(
                        f"切换快捷模式状态时出错，帖子ID: {thread_to_update.id}",
                        exc_info=e,
                    )
                    await interaction.followup.send(
                        "❌ 切换状态时发生内部错误。", ephemeral=True
                    )


# class SetReactionEmojiModal(discord.ui.Modal, title="设置反应表情"):
#     """用于设置自定义反应表情的模态框。"""
#
#     def __init__(self, service: "ManagementService", thread: "Thread"):
#         super().__init__()
#         self.service = service
#         self.thread = thread
#
#         self.emoji_input = discord.ui.TextInput(
#             label="反应表情",
#             placeholder="输入一个emoji，例如: 👍, 🔥, 🎉 (留空则清除)",
#             default=thread.reaction_emoji or "",
#             style=discord.TextStyle.short,
#             required=False,
#             max_length=50,
#         )
#         self.add_item(self.emoji_input)
#
#     async def on_submit(self, interaction: discord.Interaction):
#         await interaction.response.defer(ephemeral=True)
#         emoji = self.emoji_input.value.strip()
#         # 验证：如果非空，确保是单个有效的emoji（简单检查）
#         if emoji and len(emoji) > 10:  # 粗略检查，实际可以更严格
#             await interaction.followup.send(
#                 "❌ 请输入一个有效的emoji（长度不超过10个字符）。", ephemeral=True
#             )
#             return
#
#         async with AsyncSessionLocal() as session:
#             try:
#                 # 获取最新的帖子状态
#                 fresh_thread = await self.service.thread_repo.get(
#                     session, id=self.thread.id
#                 )
#                 if not fresh_thread:
#                     await interaction.followup.send(
#                         "❌ 错误：找不到帖子。", ephemeral=True
#                     )
#                     return
#
#                 update_data = {"reaction_emoji": emoji if emoji else None}
#                 await self.service.thread_repo.update(
#                     session,
#                     db_obj=fresh_thread,
#                     obj_in=update_data,
#                 )
#                 await session.commit()
#
#                 # 刷新整个管理面板
#                 refreshed_panel = await self.service.handle_management_request(
#                     session, interaction=interaction
#                 )
#                 await interaction.edit_original_response(**refreshed_panel)
#
#             except Exception as e:
#                 await session.rollback()
#                 logger.error(
#                     f"设置反应表情时出错，帖子ID: {self.thread.id}",
#                     exc_info=e,
#                 )
#                 await interaction.followup.send(
#                     "❌ 设置反应表情时发生内部错误。", ephemeral=True
#                 )
