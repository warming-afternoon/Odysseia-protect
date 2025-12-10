# -*- coding: utf-8 -*-
"""
上传功能的 UI 组件 (View 和 Modal)
"""

import logging
from typing import Optional, TYPE_CHECKING

import discord

from src.database.database import AsyncSessionLocal
from src.database.repositories.user import UserRepository

if TYPE_CHECKING:
    from src.services.upload_service import UploadService

logger = logging.getLogger(__name__)


class PrivacyPolicyView(discord.ui.View):
    """一个包含同意和拒绝隐私协议按钮的视图。"""

    def __init__(
        self,
        user_repo: UserRepository,
        service: "UploadService",
        mode: str,
        file: Optional[discord.Attachment],
        message_link: Optional[str],
    ):
        super().__init__(timeout=300)  # 5分钟后超时
        self.user_repo = user_repo
        self.service = service
        self.mode = mode
        self.file = file
        self.message_link = message_link

    @discord.ui.button(label="同意", style=discord.ButtonStyle.success)
    async def agree(self, interaction: discord.Interaction, button: discord.ui.Button):
        """处理用户同意协议的事件，并立即弹出上传表单。"""
        # 1. 更新数据库
        async with AsyncSessionLocal() as session:
            user = await self.user_repo.get(session, id=interaction.user.id)
            if user:
                update_data = {"has_agreed_to_privacy_policy": True}
                await self.user_repo.update(session, db_obj=user, obj_in=update_data)
                await session.commit()

        # 2. 弹出上传表单，这是对按钮点击交互的唯一响应。
        # 根据模式决定弹出哪个模态框
        if self.mode == "secure":
            modal = SecureUploadModal(
                service=self.service,
                file=self.file,
            )
        else:  # normal mode
            modal = NormalUploadModal(
                service=self.service, message_link=self.message_link
            )
        await interaction.response.send_modal(modal)

        # 3. 禁用原始消息中的按钮。
        # 因为交互已经被消耗，我们不能再用 interaction.edit_original_response。
        # 相反，我们直接编辑交互所附着的消息本身 (interaction.message)。
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        # 安全检查，确保 message 对象存在
        if interaction.message:
            await interaction.message.edit(view=self)

    @discord.ui.button(label="拒绝", style=discord.ButtonStyle.danger)
    async def disagree(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """处理用户拒绝协议的事件。"""
        await interaction.response.send_message(
            "⚠️ 您已拒绝隐私协议。在您同意之前，您将无法使用文件上传功能。",
            ephemeral=True,
        )
        # 禁用所有按钮
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await interaction.edit_original_response(view=self)


class NormalUploadModal(discord.ui.Modal, title="上传普通文件 - 填写信息"):
    """用于普通文件上传的模态框，只包含版本信息。"""

    def __init__(self, service: "UploadService", message_link: Optional[str]):
        super().__init__()
        self.service = service
        self.message_link = message_link

        self.version_info_input = discord.ui.TextInput(
            label="版本信息",
            placeholder="例如：v1.0.0, 2024-01-01 Final",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
        )
        self.add_item(self.version_info_input)

    async def on_submit(self, interaction: discord.Interaction):
        """当用户提交模态框时，调用服务层完成上传流程。"""
        await interaction.response.send_message(
            "⏳ 正在处理您的上传，请稍候...", ephemeral=True
        )
        async with AsyncSessionLocal() as session:
            result_message = await self.service.handle_upload_submission(
                session,
                interaction=interaction,
                mode="normal",
                message_link=self.message_link,
                version_info=self.version_info_input.value,
                password=None,  # 普通文件没有密码
            )
        await interaction.edit_original_response(content=result_message)


class SecureUploadModal(discord.ui.Modal, title="上传受保护文件 - 填写信息"):
    """用于受保护文件上传的模态框，包含版本和密码信息。"""

    def __init__(self, service: "UploadService", file: Optional[discord.Attachment]):
        super().__init__()
        self.service = service
        self.file = file

        self.version_info_input = discord.ui.TextInput(
            label="版本信息",
            placeholder="例如：v1.0.0, 2024-01-01 Final",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
        )
        self.password_input = discord.ui.TextInput(
            label="下载密码 (留空则无密码)",
            placeholder="例如：123456",
            style=discord.TextStyle.short,
            required=False,
            max_length=50,
        )
        self.add_item(self.version_info_input)
        self.add_item(self.password_input)

    async def on_submit(self, interaction: discord.Interaction):
        """当用户提交模态框时，调用服务层完成上传流程。"""
        await interaction.response.send_message(
            "⏳ 正在处理您的上传，请稍候...", ephemeral=True
        )
        async with AsyncSessionLocal() as session:
            result_message = await self.service.handle_upload_submission(
                session,
                interaction=interaction,
                mode="secure",
                file=self.file,
                version_info=self.version_info_input.value,
                password=self.password_input.value or None,
            )
        await interaction.edit_original_response(content=result_message)
