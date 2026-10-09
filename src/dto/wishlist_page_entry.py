from dataclasses import dataclass
from datetime import datetime

from src.dto.resource_dto import ResourceDTO


@dataclass(frozen=True)
class WishlistPageEntry:
    """单个心愿单收藏项的资源快照和取链结果。"""

    item_id: int
    """心愿单关联记录的数据库主键，用于按页面快照批量删除。"""

    resource: ResourceDTO
    """独立资源 DTO，不持有 ORM 关联或数据库会话。"""

    created_at: datetime
    """用户将资源加入心愿单的时间。"""

    url: str | None
    """本次成功取得的交付链接，取链失败时为空，不写入观察日志。"""

    error: str | None = None
    """取链失败时可展示给用户的说明，成功时为空。"""
