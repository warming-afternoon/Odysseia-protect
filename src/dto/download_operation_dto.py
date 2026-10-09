from dataclasses import dataclass

from src.dto.rate_limit_result import RateLimitResult


@dataclass
class DownloadOperationDTO:
    """当前交互的观察日志上下文，仅包含可记录的业务元数据。"""

    user_id: int
    """下载者的 Discord 用户 ID，跨帖子、跨服务器共享同一额度。"""

    operation: str
    """当前下载链路操作名称，用于区分入口、分页、选择和交付。"""

    guild_id: int | None = None
    """当前操作或资源所属的 Discord 服务器 ID，未知时为空。"""

    thread_id: int | None = None
    """公开来源帖子的 Discord 频道 ID，不是数据库主键。"""

    resource_id: int | None = None
    """当前单资源交付的数据库主键，整页心愿单操作时可为空。"""

    page: int | None = None
    """本次查看或交付的实际页码，从一开始，不适用时为空。"""

    outcome: str = "success"
    """操作结果标记，只记录预定义状态，不保存密码、令牌或下载链接。"""

    result: RateLimitResult | None = None
    """本次交付额度检查结果，纯浏览操作在结束时单独查询观察状态。"""
