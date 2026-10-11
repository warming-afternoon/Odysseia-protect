from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.enums import UploadMode


class DownloadResourceOptionDTO(BaseModel):
    """不包含密码的资源版本列表项。"""

    model_config = ConfigDict(from_attributes=True, frozen=True)
    """允许在会话内从 ORM 属性构建不可修改的列表项，供会话关闭后渲染。"""

    id: int
    """资源版本的数据库主键 ID，作为下拉菜单选项值和下载详情查询条件。"""

    filename: str | None
    """资源的显示文件名；为空时下拉菜单使用 N/A 占位。"""

    version_info: str
    """作者或协作者填写的版本说明，用于资源列表和下拉菜单标签。"""

    created_at: datetime
    """资源版本登记到 Bot 的时间，用于最新优先排序和显示上传日期。"""

    upload_mode: UploadMode
    """上传模式：NORMAL 引用公开消息，SECURE 保存于私密仓库，用于分组和标识。"""

    source_message_id: int
    """资源源消息的 Discord ID，普通资源据此构建公开消息跳转链接。"""

    trace_enabled: bool = False
    """是否开启动态溯源；开启时显示溯源标识，下载前进入告知与确认流程。"""
