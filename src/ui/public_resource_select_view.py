from src.dto.download_page_dto import DownloadPageDTO
from src.enums.download_response_mode import DownloadResponseMode
from src.ui.download_pagination_view import DownloadPaginationView


class PublicResourceSelectView(DownloadPaginationView):
    """公开的限时入口，分页和下载均进入个人面板。"""

    response_mode = DownloadResponseMode.CREATE_PRIVATE_PANEL

    def __init__(self, page_data: DownloadPageDTO, *, timeout: float = 60.0):
        """创建可供多人使用的短期公开入口。"""
        super().__init__(page_data, timeout=timeout)
        self.render_page()
