from pydantic import BaseModel, ConfigDict

from src.enums import UploadMode


class ManagementResourceDTO(BaseModel):
    """保存当前管理页资源，避免交互时访问已关闭的数据库会话。"""

    model_config = ConfigDict(from_attributes=True, frozen=True)
    """允许从 ORM 资源构建不可修改的管理列表项，供会话关闭后使用。"""

    id: int
    """资源版本的数据库主键 ID，用于选择及后续管理操作。"""

    version_info: str
    """资源版本说明，用于列表、下拉菜单和编辑表单。"""

    filename: str | None = None
    """资源显示文件名，用于列表和删除确认信息。"""

    author_note: str | None = None
    """作者备注，用于编辑表单回显。"""

    source_message_id: int
    """源消息的 Discord ID，用于普通资源跳转和换源校验。"""

    upload_mode: UploadMode
    """资源上传模式，用于分组及判断是否支持换源。"""

    download_count: int = 0
    """资源下载次数，用于管理列表展示。"""
