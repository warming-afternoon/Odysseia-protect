import os
from pathlib import Path
from typing import AsyncGenerator
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, String, Table, Column, event, inspect, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
import logging

from src.enums.path import DB_PATH

# --- 数据库配置 ---
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DB_PATH}")

# --- SQLAlchemy 引擎和会话设置 ---

# 创建异步数据库引擎
# echo=True 会打印所有执行的SQL语句，便于调试
engine = create_async_engine(DATABASE_URL)


@event.listens_for(engine.sync_engine, "connect")
def _enable_wal(dbapi_connection, connection_record):
    """为每个新连接启用 WAL 和外键约束。"""
    dbapi_connection.execute("PRAGMA journal_mode=WAL")
    dbapi_connection.execute("PRAGMA foreign_keys=ON")

# 创建一个异步会话生成器
# expire_on_commit=False 防止在提交后 ORM 对象的属性被过期
AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)

# --- 声明式模型基类 ---
# 我们所有的 ORM 模型都将继承这个 Base 类
Base = declarative_base()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    一个依赖注入函数，用于获取数据库会话。
    在每个请求/操作的生命周期内提供一个会话，并在结束后自动关闭。
    """
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """
    初始化空数据库；已有数据库必须先由 Alembic 升级到最新版本。
    """
    db_path = engine.url.database
    if db_path and db_path != ":memory:":  # 确保不是内存数据库
        # 获取目录部分
        db_dir = os.path.dirname(db_path)
        # 如果目录非空且不存在，则创建它
        if db_dir and not os.path.exists(db_dir):
            logging.info(f"数据库目录 '{db_dir}' 不存在，正在创建...")
            os.makedirs(db_dir)

    project_root = Path(__file__).resolve().parents[2]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option(
        "script_location", str(project_root / "alembic")
    )
    expected_head = ScriptDirectory.from_config(alembic_config).get_current_head()
    if expected_head is None:
        raise RuntimeError("无法确定 Alembic head revision。")

    def _initialize_or_validate(sync_connection):
        inspector = inspect(sync_connection)
        tables = set(inspector.get_table_names())
        domain_tables = tables - {"alembic_version"}

        if not domain_tables:
            Base.metadata.create_all(sync_connection)
            version_metadata = MetaData()
            version_table = Table(
                "alembic_version",
                version_metadata,
                Column("version_num", String(32), primary_key=True, nullable=False),
            )
            version_metadata.create_all(sync_connection)
            sync_connection.execute(
                version_table.insert().values(version_num=expected_head)
            )
            logging.info(
                "已初始化空数据库并标记 Alembic 版本为 %s。", expected_head
            )
            return

        if "alembic_version" not in tables:
            raise RuntimeError(
                "检测到未纳入 Alembic 管理的现有数据库。"
                "请先停止 Bot 并运行 `python migrate.py`。"
            )

        version_table = Table(
            "alembic_version",
            MetaData(),
            Column("version_num", String(32), primary_key=True, nullable=False),
        )
        current_version = sync_connection.execute(
            select(version_table.c.version_num)
        ).scalar_one_or_none()
        if current_version != expected_head:
            raise RuntimeError(
                f"数据库版本为 {current_version or '未知'}，"
                f"代码要求 {expected_head}。请先运行 `python migrate.py`。"
            )

    async with engine.begin() as conn:
        await conn.run_sync(_initialize_or_validate)
