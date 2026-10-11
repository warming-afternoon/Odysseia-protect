"""受保护资源单文件和多附件上传表单。"""

from typing import Optional, Union, List, TYPE_CHECKING

import discord

from src.config import AUTHOR_NOTE_MAX_LENGTH
from src.database.database import AsyncSessionLocal
from src.ui.upload_modal import UploadModal

if TYPE_CHECKING:
    from src.services.upload_service import UploadService


class SecureUploadModal(UploadModal, title="上传受保护文件 - 填写信息"):
    """用于受保护文件上传的模态框，包含版本、作者备注及溯源设置。"""

    def __init__(
        self,
        service: "UploadService",
        files: Union[discord.Attachment, List[discord.Attachment]],
        user_id: int,
        source_message: Optional[discord.Message] = None,
    ):
        """构建受保护资源的版本、作者备注及溯源设置表单。"""
        # 单文件与多附件共用同一备注填写入口。
        super().__init__()
        self.user_id = user_id
        self.service = service
        self.files = files
        self.source_message = source_message

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
        self.trace_input = discord.ui.TextInput(
            label="动态溯源（输入“开启”以启用，留空则关闭）",
            placeholder="仅支持不超过 25 MB 的 PNG 角色卡",
            style=discord.TextStyle.short,
            required=False,
            max_length=10,
        )
        self.add_item(self.version_info_input)
        self.add_item(self.author_note_input)
        self.add_item(self.trace_input)

    async def on_submit(self, interaction: discord.Interaction):
        """当用户提交模态框时，调用服务层完成上传流程。"""
        if not await self.check_actor(interaction):
            return
        await interaction.response.send_message(
            "⏳ 正在处理您的上传，请稍候...", ephemeral=True
        )
        # 统一解析同一表单的溯源开关，避免单文件与多附件行为分歧。
        trace_enabled = self.trace_input.value.strip().casefold() in {"开启", "是", "yes", "true", "1"}
        async with AsyncSessionLocal() as session:
            if isinstance(self.files, list):
                # 来自上下文菜单的多文件上传
                result_message = (
                    await self.service.handle_secure_upload_submission_from_message(
                        session,
                        interaction=interaction,
                        attachments=self.files,
                        version_info=self.version_info_input.value,
                        author_note=self.author_note_input.value,
                        trace_enabled=trace_enabled,
                        source_message=self.source_message,
                    )
                )
            else:
                # 来自 /上传 命令的单文件上传
                result_message = await self.service.handle_upload_submission(
                    session,
                    interaction=interaction,
                    mode="secure",
                    file=self.files,
                    version_info=self.version_info_input.value,
                    author_note=self.author_note_input.value,
                    trace_enabled=trace_enabled,
                )
        await interaction.edit_original_response(content=result_message)
