"""普通资源上传表单。"""

from typing import Optional, TYPE_CHECKING

import discord

from src.config import AUTHOR_NOTE_MAX_LENGTH
from src.database.database import AsyncSessionLocal
from src.ui.upload_modal import UploadModal

if TYPE_CHECKING:
    from src.services.upload_service import UploadService


class NormalUploadModal(UploadModal, title="上传普通文件 - 填写信息"):
    """用于普通文件上传的模态框，包含版本信息和可选作者备注。"""

    def __init__(self, service: "UploadService", message_link: Optional[str], user_id: int):
        """构建普通资源版本信息和可选作者备注表单。"""
        # 备注长度仅在用户填写和提交入口限制。
        super().__init__()
        self.user_id = user_id
        self.service = service
        self.message_link = message_link

        self.version_info_input = discord.ui.TextInput(
            label="版本信息",
            placeholder="例如：v1.0.0, 2024-01-01 Final",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
        )
        self.author_note_input = discord.ui.TextInput(
            label="作者备注（可选）",
            placeholder="填写使用说明、注意事项等，最多 400 个字符",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=AUTHOR_NOTE_MAX_LENGTH,
        )
        self.add_item(self.version_info_input)
        self.add_item(self.author_note_input)

    async def on_submit(self, interaction: discord.Interaction):
        """当用户提交模态框时，调用服务层完成上传流程。"""
        if not await self.check_actor(interaction):
            return
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
                author_note=self.author_note_input.value,
            )
        await interaction.edit_original_response(content=result_message)
