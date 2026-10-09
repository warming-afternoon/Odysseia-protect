from pydantic import BaseModel


class ResourceUpdate(BaseModel):
    """按已提供字段更新资源信息和保护设置的参数。"""

    version_info: str | None = None
    """要更新的字段；未提供时不参与更新。资源版本说明，用于资源选择和管理界面展示。"""

    description: str | None = None
    """要更新的字段；未提供时不参与更新。可选资源说明，未提供时为空。"""

    password: str | None = None
    """要更新的字段；未提供时不参与更新。可选下载密码，未设置时无需密码验证；不应写入观察日志。"""

    trace_enabled: bool | None = None
    """要更新的字段；未提供时不参与更新。是否启用下载者专属的动态溯源交付，默认关闭。"""
