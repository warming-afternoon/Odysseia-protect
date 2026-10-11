from typing import Optional
from pydantic import BaseModel

from src.enums import UploadMode
from src.enums import SourceStatus


class ResourceDTO(BaseModel):
    """可脱离数据库会话使用的资源下载详情。"""

    id: int
    """资源版本的数据库主键 ID，用于下载详情查询、管理和心愿单引用。"""

    filename: Optional[str] = None
    """资源的显示文件名；为空时下载面板使用未命名文件占位。"""

    version_info: str = "未提供"
    """作者或协作者填写的版本说明，用于下载面板展示；默认显示未提供。"""

    author_note: Optional[str] = None
    """资源作者备注；为空时下载面板不展示。"""

    source_message_id: int
    """源消息的 Discord ID；普通资源指向公开消息，受保护资源指向私密仓库消息。"""

    warehouse_thread_id: Optional[int] = None
    """私密仓库帖子的 Discord 频道 ID；未创建仓库时为空。"""

    public_thread_id: int
    """资源所属公开来源帖子的 Discord 频道 ID，用于资源归属校验和原帖跳转。"""

    author_id: Optional[int] = None
    """原帖主的 Discord 用户 ID；协作者维护时仍保留原作者，未知时为空。"""

    guild_id: Optional[int] = None
    """公开来源所在的 Discord 服务器 ID；旧数据尚未回填时可为空。"""

    public_thread_name: Optional[str] = None
    """公开来源帖子的标题快照；尚未获取或回填时可为空。"""

    source_status: SourceStatus = SourceStatus.UNKNOWN
    """公开来源的已知状态：未确认、可用或已删除；默认未确认。"""

    upload_mode: Optional[UploadMode] = None
    """资源上传模式，决定取链时使用公开来源或私密仓库；未知时为空。"""

    trace_enabled: bool = False
    """是否启用下载者专属的动态溯源交付；默认关闭。"""
