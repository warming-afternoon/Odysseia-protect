from pydantic import ConfigDict

from .user_base import UserBase


class UserInDB(UserBase):
    """可从用户 ORM 属性构建的独立数据。"""

    model_config = ConfigDict(from_attributes=True)
    """允许读取 ORM 对象的属性，转换后可脱离数据库会话使用。"""
