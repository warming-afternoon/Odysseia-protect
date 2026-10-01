from pydantic import BaseModel, ConfigDict

from src.dto.download_resource_option_dto import DownloadResourceOptionDTO

DOWNLOAD_PAGE_SIZE = 25
"""每页最多展示的资源版本数量，对应 Discord 下拉菜单的选项上限。"""


class DownloadPageDTO(BaseModel):
    """可在数据库会话关闭后使用的下载分页数据。"""

    model_config = ConfigDict(frozen=True)
    """分页数据不可修改，避免不同面板共享数据时互相影响。"""

    public_thread_id: int
    """公开来源帖子的 Discord 频道 ID，用于翻页查询和构建资源跳转链接。"""

    guild_id: int | None = None
    """来源服务器的 Discord ID，用于构建消息跳转链接；元数据未记录时可为空。"""

    items: tuple[DownloadResourceOptionDTO, ...] = ()
    """当前页的资源版本，按登记时间和主键倒序排列；无资源时为空元组。"""

    page: int = 1
    """实际返回的页码，从 1 开始；请求超过总页数时回退到有效末页。"""

    total: int = 0
    """当前帖子中的资源版本总数，超过每页上限时显示分页按钮。"""

    max_page: int = 1
    """资源列表的总页数；无资源时仍为 1，便于空态面板统一处理。"""
