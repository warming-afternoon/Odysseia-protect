from pydantic import BaseModel

from src.enums import SourceStatus


class ThreadBase(BaseModel):
    """帖子来源和私密仓库关联的基础数据。"""

    public_thread_id: int
    """公开来源帖子的 Discord 频道 ID，不是帖子记录的数据库主键。"""

    author_id: int
    """原帖主的 Discord 用户 ID，协作者维护时仍保留原作者。"""

    guild_id: int | None = None
    """公开来源所在的 Discord 服务器 ID，历史元数据缺失时可为空。"""

    public_thread_name: str | None = None
    """公开来源标题的快照，尚未获取时可为空。"""

    source_status: SourceStatus = SourceStatus.UNKNOWN
    """公开来源状态，默认 unknown，确认来源后记录 active 或 deleted。"""

    warehouse_thread_id: int | None = None
    """私密仓库帖子的 Discord 频道 ID，未建立仓库时可为空。"""

    download_panel_message_id: int | None = None
    """历史下载面板的 Discord 消息 ID，未登记时可为空。"""
