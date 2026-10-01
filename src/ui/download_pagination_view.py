import asyncio
import logging
from collections.abc import Callable

import discord

from src.database.database import AsyncSessionLocal
from src.dto.download_page_dto import DownloadPageDTO
from src.enums.download_response_mode import DownloadResponseMode
from src.qo.download_page_qo import DownloadPageQo
from src.ui.download_embed_builder import DownloadEmbedBuilder
from src.ui.resource_select import ResourceSelect

logger = logging.getLogger(__name__)


class DownloadPaginationView(discord.ui.View):
    """共用资源页渲染与分页交互。"""

    response_mode: DownloadResponseMode

    def __init__(self, page_data: DownloadPageDTO, *, timeout: float, user_id: int | None = None):
        """保存独立分页数据并初始化面板状态。"""
        super().__init__(timeout=timeout)
        # 面板只保存数据对象，数据库会话在每次交互内创建。
        self.user_id = user_id
        self.state_lock = asyncio.Lock()
        self.page_data = page_data
        self.resource_list_embed = DownloadEmbedBuilder.build_page_embed(page_data)

    def render_page(self):
        """根据当前页重建下拉框及必要的分页按钮。"""
        self.clear_items()
        self.resource_list_embed = DownloadEmbedBuilder.build_page_embed(self.page_data)
        self.add_item(ResourceSelect(
            self.page_data.items, public_thread_id=self.page_data.public_thread_id,
            resource_list_embed=self.resource_list_embed, response_mode=self.response_mode,
            private_view_factory=self.create_private_view,
        ))
        # 一页能容纳所有资源时不添加分页行。
        if self.page_data.total <= 25:
            return
        buttons = (
            ("⏮️", lambda page: 1, self.page_data.page == 1),
            ("◀️", lambda page: page - 1, self.page_data.page == 1),
            (f"{self.page_data.page}/{self.page_data.max_page}", None, False),
            ("▶️", lambda page: page + 1, self.page_data.page == self.page_data.max_page),
            ("⏭️", lambda page: self.page_data.max_page, self.page_data.page == self.page_data.max_page),
        )
        for label, target, disabled in buttons:
            button = discord.ui.Button(
                label=label, row=1, disabled=disabled,
                style=discord.ButtonStyle.primary if target is None else discord.ButtonStyle.secondary,
            )
            button.callback = self.show_page_jump_modal if target is None else self.page_callback(target)
            self.add_item(button)

    def page_callback(self, target: Callable[[int], int]):
        """为按钮生成在锁内计算目标页的回调。"""
        async def callback(interaction: discord.Interaction):
            """根据最新面板状态执行分页操作。"""
            await self.go_to_page(interaction, target)
        return callback

    async def show_page_jump_modal(self, interaction: discord.Interaction):
        """弹出当前面板的页码输入窗口。"""
        from src.ui.download_page_jump_modal import DownloadPageJumpModal

        await interaction.response.send_modal(DownloadPageJumpModal(self))

    async def go_to_page(self, interaction: discord.Interaction, target: int | Callable[[int], int]):
        """查询目标页并原地更新私密面板或创建个人面板。"""
        is_public = self.response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL
        # 先应答交互，再串行查询和编辑，避免等待锁时交互过期。
        if is_public:
            await interaction.response.defer(ephemeral=True, thinking=True)
        else:
            await interaction.response.defer()
        async with self.state_lock:
            try:
                page = target(self.page_data.page) if callable(target) else target
                async with AsyncSessionLocal() as session:
                    page_data = await interaction.client.download_service.get_page(session, DownloadPageQo(
                        public_thread_id=self.page_data.public_thread_id, page=max(1, page),
                    ))
                if page_data.guild_id is None:
                    page_data = page_data.model_copy(update={"guild_id": self.page_data.guild_id})
                # 公开入口不修改自身，每位用户获得独立的私密视图。
                if is_public:
                    candidate = self.create_private_view(page_data, user_id=interaction.user.id)
                    await interaction.edit_original_response(
                        content=None, embeds=[candidate.resource_list_embed], view=candidate, attachments=[],
                    )
                else:
                    previous_page = self.page_data
                    previous_selection = self.selected_resource_id
                    previous_children = tuple(self.children)
                    previous_embed = self.resource_list_embed
                    previous_add_button = self.add_button
                    previous_remove_button = self.remove_button
                    self.page_data = page_data
                    self.render_page()
                    try:
                        await interaction.edit_original_response(
                            content=None, embeds=[self.resource_list_embed], view=self, attachments=[],
                        )
                    except Exception:
                        # 消息编辑失败时恢复原页及收藏按钮状态。
                        self.page_data = previous_page
                        self.resource_list_embed = previous_embed
                        self.clear_items()
                        for item in previous_children:
                            self.add_item(item)
                        self.add_button = previous_add_button
                        self.remove_button = previous_remove_button
                        self.selected_resource_id = previous_selection
                        raise
            except Exception:
                logger.exception("资源分页失败：thread=%s", self.page_data.public_thread_id)
                if is_public:
                    await interaction.edit_original_response(
                        content="❌ 获取资源页面失败，请稍后重试。", embeds=[], view=None,
                    )
                else:
                    await interaction.followup.send("❌ 获取资源页面失败，请稍后重试。", ephemeral=True)

    def create_private_view(self, page_data: DownloadPageDTO | None = None, *, user_id: int | None = None):
        """为当前页或指定页创建独立的私密下载面板。"""
        from src.ui.resource_select_view import ResourceSelectView

        return ResourceSelectView(page_data or self.page_data, user_id=user_id or self.user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """限制个人面板仅由所属用户操作。"""
        if self.user_id is None or interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("❌ 这不是您的下载面板。", ephemeral=True)
        return False

    async def on_timeout(self):
        """停止过期面板而不编辑失效的交互消息。"""
        self.stop()
