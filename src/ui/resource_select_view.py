import discord

from src.database.database import AsyncSessionLocal
from src.dto.download_page_dto import DownloadPageDTO
from src.enums.download_response_mode import DownloadResponseMode
from src.ui.add_to_wishlist_button import AddToWishlistButton
from src.ui.download_pagination_view import DownloadPaginationView
from src.ui.remove_from_wishlist_button import RemoveFromWishlistButton


class ResourceSelectView(DownloadPaginationView):
    """版本分页、加入和移除心愿单的私密下载面板。"""

    response_mode = DownloadResponseMode.EDIT_PRIVATE_PANEL

    def __init__(self, page_data: DownloadPageDTO, *, user_id: int | None = None):
        """创建当前页的版本选择及心愿单组件。"""
        super().__init__(page_data, timeout=14400.0, user_id=user_id)
        self.selected_resource_id: int | None = None
        self.render_page()

    def render_page(self):
        """重建当前页组件并清除原资源授权。"""
        super().render_page()
        self.selected_resource_id = None
        # 心愿单操作单独占一行，避免挤占五个分页按钮的位置。
        self.add_button = AddToWishlistButton()
        self.remove_button = RemoveFromWishlistButton()
        self.add_button.row = self.remove_button.row = 2
        self.add_item(self.add_button)
        self.add_item(self.remove_button)

    def clear_authorized_selection(self):
        """清除下载授权并禁用当前资源的心愿单操作。"""
        self.selected_resource_id = None
        self.add_button.disabled = self.remove_button.disabled = True

    def set_wishlist_state(self, is_wishlisted: bool):
        """根据当前资源是否已收藏更新按钮状态。"""
        self.add_button.disabled = is_wishlisted
        self.remove_button.disabled = not is_wishlisted

    async def authorize_selection(self, interaction: discord.Interaction, *, resource_id: int):
        """下载成功后记录个人资源选择并读取收藏状态。"""
        service = getattr(interaction.client, "wishlist_service", None)
        if service is None:
            raise RuntimeError("Bot 未配置心愿单服务。")
        # 收藏查询使用短会话，面板只保存选择标识。
        async with AsyncSessionLocal() as session:
            is_wishlisted = await service.is_wishlisted(
                session, user_id=interaction.user.id, resource_id=resource_id,
            )
        self.user_id = interaction.user.id
        self.selected_resource_id = resource_id
        self.set_wishlist_state(is_wishlisted)
