import os
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

# --- 数据库配置 ---
# 建议将数据库URL存储在环境变量中
# 为了方便演示，这里使用 SQLite。生产环境建议使用 PostgreSQL。
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot_database.db")

# --- SQLAlchemy 引擎和会话设置 ---

# 创建异步数据库引擎
# echo=True 会打印所有执行的SQL语句，便于调试
engine = create_async_engine(DATABASE_URL)

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
    初始化数据库，根据模型创建所有表。
    只应在应用程序启动时运行一次。
    """
    async with engine.begin() as conn:
        # await conn.run_sync(Base.metadata.drop_all) # 如果需要，可以取消注释以在每次启动时清空数据库
        await conn.run_sync(Base.metadata.create_all)
