# -*- coding: utf-8 -*-
"""
上传功能的 UI 组件 (View 和 Modal)
"""

from typing import Optional, TYPE_CHECKING, Union, List

import discord

from src.database.database import AsyncSessionLocal
from src.database.repositories.user import UserRepository
from src.utils.auth import assert_thread_manager, send_private_error
from src.ui.normal_upload_modal import NormalUploadModal
from src.ui.secure_upload_modal import SecureUploadModal
from src.ui.upload_modal import UploadModal

if TYPE_CHECKING:
    from src.services.upload_service import UploadService

# 保留旧模块的公开导出，表单实现位于各自对应模块。
__all__ = ["PrivacyPolicyView", "UploadModal", "NormalUploadModal", "SecureUploadModal"]


class PrivacyPolicyView(discord.ui.View):
    """一个包含同意和拒绝隐私协议按钮的视图。"""

    def __init__(
        self,
        user_repo: UserRepository,
        user_id: int,
        service: "UploadService",
        mode: str,
        file: Optional[Union[discord.Attachment, List[discord.Attachment]]],
        message_link: Optional[str],
        source_message: Optional[discord.Message] = None,
    ):
        super().__init__(timeout=300)  # 5分钟后超时
        self.user_id = user_id
        self.user_repo = user_repo
        self.service = service
        self.mode = mode
        self.file = file
        self.message_link = message_link
        self.source_message = source_message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await send_private_error(interaction, "此上传面板仅供发起人使用。")
            return False
        async with AsyncSessionLocal() as session:
            return await assert_thread_manager(session, interaction=interaction,
                                               thread_repo=self.service.thread_repo)

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
            assert self.file is not None
            modal = SecureUploadModal(
                service=self.service,
                user_id=self.user_id,
                files=self.file,
                source_message=self.source_message,
            )
        else:  # normal mode
            modal = NormalUploadModal(
                service=self.service, message_link=self.message_link, user_id=self.user_id
            )
        await interaction.response.send_modal(modal)

        # 3. 禁用原始消息中的按钮。
        # 因为交互已经被消耗，我们不能再用 interaction.edit_original_response。
        # 相反，我们直接编辑交互所附着的消息本身 (interaction.message)。
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        # 安全检查，确保 message 对象存在
        # 经过测试，编辑一个已经响应过的交互的原始消息（特别是临时的）
        # 很容易导致 "Unknown Message" 错误，因此我们删除这一步。
        # 功能上，模态框已经弹出，核心流程已经完成。
        if interaction.message:
            # await interaction.message.edit(view=self)
            pass

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
