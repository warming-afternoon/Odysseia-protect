from pydantic import BaseModel

from src.enums import UploadMode


class ResourceBase(BaseModel):
    """资源登记与下载保护设置的基础数据。"""

    thread_id: int
    """资源所属帖子记录的数据库主键，作为帖子外键。"""

    upload_mode: UploadMode
    """资源登记模式，决定取链时使用公开来源或私密仓库。"""

    filename: str
    """登记的资源文件名，用于下载界面展示和交付附件命名。"""

    version_info: str
    """资源版本说明，用于资源选择和管理界面展示。"""

    source_message_id: int
    """包含源附件的 Discord 消息 ID，用于动态取得有效下载链接。"""

    description: str | None = None
    """可选资源说明，未提供时为空。"""

    author_note: str | None = None
    """可选作者备注；存储契约不限制长度，用户提交入口独立校验。"""

    password: str | None = None
    """历史下载密码的兼容字段，不再用于业务维护或交付。"""

    trace_enabled: bool = False
    """是否启用下载者专属的动态溯源交付，默认关闭。"""
