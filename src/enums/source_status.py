from enum import Enum


class SourceStatus(Enum):
    """Discord 公开来源帖子的已知状态。"""

    UNKNOWN = "unknown"
    ACTIVE = "active"
    DELETED = "deleted"
