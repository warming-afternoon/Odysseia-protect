import enum


class UploadMode(enum.Enum):
    """资源源文件所在位置及对应的上传保护模式。"""

    SECURE = "secure"
    """受保护资源，源文件保存在私密仓库，可配置下载密码或动态溯源。"""

    NORMAL = "normal"
    """普通资源，引用公开帖子中的源消息，通过下载面板获取当前有效链接。"""
