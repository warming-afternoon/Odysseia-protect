from enum import Enum


class DownloadResponseMode(Enum):
    """下载选择后的响应目标。"""

    EDIT_PRIVATE_PANEL = "edit_private_panel"
    """在当前私密下载消息中更新结果，保留当前资源页及操作按钮。"""

    CREATE_PRIVATE_PANEL = "create_private_panel"
    """从公开入口创建新的私密下载响应，不修改多人共用的公开消息。"""
