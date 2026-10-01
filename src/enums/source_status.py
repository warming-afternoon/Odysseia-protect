from enum import Enum


class SourceStatus(Enum):
    """Discord 公开来源帖子的已知状态。"""

    UNKNOWN = "unknown"
    """尚未确认来源帖子的状态，常见于未完成元数据回填的旧记录。"""

    ACTIVE = "active"
    """来源帖子存在，已通过来源同步或上传流程确认其可用状态。"""

    DELETED = "deleted"
    """来源帖子已删除，保留数据库中的来源信息供历史资源展示。"""
