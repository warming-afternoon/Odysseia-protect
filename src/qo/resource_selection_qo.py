from pydantic import BaseModel


class ResourceSelectionQo(BaseModel):
    """限定资源所属帖子的下载选择条件。"""

    public_thread_id: int
    """当前下载面板所属公开帖子的 Discord 频道 ID，用于校验资源归属。"""

    resource_id: int
    """用户选择的资源版本数据库主键 ID，服务据此重新查询下载详情。"""
