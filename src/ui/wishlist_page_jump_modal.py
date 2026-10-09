import logging
from typing import TYPE_CHECKING

import discord

from src.database.database import AsyncSessionLocal
from src.exceptions.rate_limit_exceeded import RateLimitExceeded
from src.utils.download_guard import (
    observe_download,
    send_rate_limit_message,
    update_operation,
)

if TYPE_CHECKING:
    from src.services.wishlist_service import WishlistService

logger = logging.getLogger(__name__)


class WishlistPageJumpModal(discord.ui.Modal, title="跳转到心愿单页面"):
    """心愿单页码输入与取链交互。"""

    def __init__(
        self,
        *,
        service: "WishlistService",
        user_id: int,
        max_page: int,
    ):
        """保存交互所需的用户、页面和服务依赖。"""
        # 保存交互所需的用户、页面和服务依赖。
        super().__init__(timeout=300)
        self.service = service
        self.user_id = user_id
        self.max_page = max_page
        self.page_input = discord.ui.TextInput(
            label=f"页码（1-{max_page}）",
            placeholder="请输入要跳转的页码",
            min_length=1,
            max_length=len(str(max_page)),
        )
        self.add_item(self.page_input)

    @observe_download("wishlist_page_jump")
    async def on_submit(self, interaction: discord.Interaction):
        """校验页码并取得目标页的心愿单交付。"""
        # 校验页码并取得目标页的心愿单交付。
        try:
            page = int(self.page_input.value)
        except ValueError:
            page = 0
        if not 1 <= page <= self.max_page:
            await interaction.response.send_message(
                f"❌ 请输入 1 到 {self.max_page} 之间的页码。",
                ephemeral=True,
            )
            return

        update_operation(page=page)
        await interaction.response.defer()
        try:
            async with AsyncSessionLocal() as session:
                render = await self.service.build_render(
                    session,
                    user_id=self.user_id,
                    page=page,
                )
        except RateLimitExceeded as error:
            await send_rate_limit_message(interaction, error)
            return
        attachments = [render.file] if render.file else []
        await interaction.edit_original_response(
            view=render.view,
            attachments=attachments,
        )
