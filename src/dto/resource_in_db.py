from pydantic import ConfigDict

from .resource_base import ResourceBase


class ResourceInDB(ResourceBase):
    """可从资源 ORM 属性构建的独立数据。"""

    id: int
    """该记录的数据库主键 ID。"""

    model_config = ConfigDict(from_attributes=True)
    """允许读取 ORM 对象的属性，转换后可脱离数据库会话使用。"""
