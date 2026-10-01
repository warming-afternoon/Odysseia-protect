from pydantic import BaseModel, Field


class DownloadPageQo(BaseModel):
    """下载资源分页查询条件。"""

    public_thread_id: int
    """要查询资源列表的公开帖子 Discord 频道 ID，不是帖子记录的数据库主键。"""

    page: int = Field(default=1, ge=1)
    """请求的页码，从 1 开始，默认查询最新资源所在的第一页；服务校正超界页码。"""
