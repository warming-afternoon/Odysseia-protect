from enum import Enum


class DownloadPanelMode(Enum):
    """下载请求所构建的面板类型。"""

    PRIVATE = "private"
    """私密下载面板，仅供发起用户浏览版本、下载和管理心愿单。"""

    PUBLIC_GATEWAY = "public_gateway"
    """公开限时下载入口，用户选择版本或翻页后进入各自的私密面板。"""
