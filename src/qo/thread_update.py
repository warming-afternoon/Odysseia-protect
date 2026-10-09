from pydantic import BaseModel

from src.enums import SourceStatus


class ThreadUpdate(BaseModel):
    """按已提供字段更新帖子元数据的参数。"""

    guild_id: int | None = None
    """要更新的字段；未提供时不参与更新。公开来源所在的 Discord 服务器 ID，历史元数据缺失时可为空。"""

    public_thread_name: str | None = None
    """要更新的字段；未提供时不参与更新。公开来源标题的快照，尚未获取时可为空。"""

    source_status: SourceStatus | None = None
    """要更新的字段；未提供时不参与更新。公开来源状态，默认 unknown，确认来源后记录 active 或 deleted。"""

    warehouse_thread_id: int | None = None
    """要更新的字段；未提供时不参与更新。私密仓库帖子的 Discord 频道 ID，未建立仓库时可为空。"""

    download_panel_message_id: int | None = None
    """要更新的字段；未提供时不参与更新。历史下载面板的 Discord 消息 ID，未登记时可为空。"""
