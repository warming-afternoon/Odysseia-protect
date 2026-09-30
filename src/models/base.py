"""所有 ORM 模型共用的声明式基类。"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()
"""所有模型共用的 ORM 注册表和 MetaData，供建表及 Alembic 迁移比较使用。"""
