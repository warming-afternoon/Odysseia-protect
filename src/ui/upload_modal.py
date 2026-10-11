"""上传表单的发起人检查。"""

import discord

from src.utils.auth import send_private_error


class UploadModal(discord.ui.Modal):
    """上传表单绑定发起人；提交权限由服务层实时校验。"""

    user_id: int
    """上传表单的原始发起人 Discord ID。"""

    async def check_actor(self, interaction: discord.Interaction) -> bool:
        """验证上传表单只能由原发起人提交。"""
        # 提交权限仍由服务层实时查询协作者授权。
        if interaction.user.id == self.user_id:
            return True
        # 非发起人的提交通过私密响应提示，不改动公共消息。
        await send_private_error(
            interaction, "此上传表单仅供发起人使用。",
        )
        return False
