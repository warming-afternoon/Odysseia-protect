"""资源版本及作者备注的管理表单。"""

import logging
from typing import TYPE_CHECKING

import discord

from src.config import AUTHOR_NOTE_MAX_LENGTH
from src.database.database import AsyncSessionLocal
from src.models import Resource
from src.utils.auth import send_private_error

if TYPE_CHECKING:
    from src.services.management_service import ManagementService
    from src.ui.management_ui import ManagementView

logger = logging.getLogger(__name__)


class ManagementModal(discord.ui.Modal, title="编辑资源信息"):
    """一个用于编辑资源信息的弹出式模态框。"""

    def __init__(
        self,
        resource: Resource,
        service: "ManagementService",
        management_view: "ManagementView",
    ):
        """回显资源版本及作者备注并限制用户输入长度。"""
        # 历史密码只保留在数据库，不进入编辑表单。
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
        self.author_note_input = discord.ui.TextInput(
            label="作者备注（留空则清除）",
            default=self.resource.author_note or "",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=AUTHOR_NOTE_MAX_LENGTH,
        )

        self.add_item(self.version_info_input)
        self.add_item(self.author_note_input)

    async def on_submit(self, interaction: discord.Interaction):
        """当用户提交模态框时，调用服务层更新资源。"""
        from src.ui.management_ui import check_management_actor

        # 同时限制原发起人和原帖子，服务层重新校验维护权限。
        if not await check_management_actor(interaction, self.management_view.original_interaction):
            return
        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            try:
                updated = await self.service.update_resource(
                    session,
                    resource_id=self.resource.id,
                    interaction=interaction,
                    version_info=self.version_info_input.value,
                    author_note=self.author_note_input.value,
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
            except (PermissionError, ValueError) as exc:
                await session.rollback()
                await send_private_error(interaction, str(exc))
            except Exception as e:
                # 如果发生错误，回滚事务
                await session.rollback()
                logger.error(f"更新资源 {self.resource.id} 时发生错误", exc_info=e)
                await interaction.followup.send(
                    "❌ 更新过程中发生内部错误。", ephemeral=True
                )

        if self.management_view is not None:
            await self.management_view.refresh(interaction)
