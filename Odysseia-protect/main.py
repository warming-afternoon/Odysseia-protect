# -*- coding: utf-8 -*-
"""
Bot 主入口文件。
"""

# --- 导入 ---
import asyncio
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from src.database import init_db
from src.database.repositories.resource import ResourceRepository
from src.database.repositories.thread import ThreadRepository
from src.database.repositories.user import UserRepository
from src.services.upload_service import UploadService
from src.services.download_service import DownloadService
from src.services.management_service import ManagementService
from src.services.reaction_wall_service import ReactionWallService

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# --- 环境变量 ---
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TEST_GUILD_ID = os.getenv("TEST_GUILD_ID")


# --- Bot 核心类 ---
class OdysseiaProtect(commands.Bot):
    """自定义 Bot 类，用于封装状态和启动逻辑。"""

    def __init__(self):
        # 定义 Bot Intents
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",  # 虽然主要用斜杠命令，但保留前缀
            intents=intents,
        )

        # --- 依赖注入 ---
        # 实例化所有服务和仓库，并将其附加到 bot 实例上
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        self.upload_service = UploadService(self, resource_repo, thread_repo, user_repo)
        self.download_service = DownloadService(
            self, resource_repo, thread_repo, user_repo
        )
        self.management_service = ManagementService(
            self, resource_repo, thread_repo, user_repo
        )
        self.reaction_wall_service = ReactionWallService(
            self, resource_repo, thread_repo, user_repo
        )

    async def setup_hook(self):
        """在 Bot 登录后执行异步初始化。"""
        if self.user:
            logger.info(f"成功以 {self.user} (ID: {self.user.id}) 的身份登录！")

        # 1. 初始化数据库
        logger.info("正在初始化数据库...")
        await init_db()
        logger.info("数据库初始化完成。")

        # 2. 动态加载 Cogs
        logger.info("开始加载 Cogs...")
        cogs_path = Path(__file__).parent / "src" / "cogs"
        for cog_file in cogs_path.glob("*.py"):
            if cog_file.is_file() and not cog_file.name.startswith("_"):
                cog_name = f"src.cogs.{cog_file.stem}"
                try:
                    await self.load_extension(cog_name)
                    logger.info(f"成功加载 Cog: {cog_name}")
                except Exception as e:
                    logger.error(f"加载 Cog {cog_name} 失败。", exc_info=e)

        # 3. 同步斜杠命令到测试服务器
        if TEST_GUILD_ID:
            logger.info(f"检测到测试服务器 ID，正在向 {TEST_GUILD_ID} 同步命令...")
            test_guild = discord.Object(id=int(TEST_GUILD_ID))
            # 将所有全局命令复制到此服务器并同步
            self.tree.copy_global_to(guild=test_guild)
            synced = await self.tree.sync(guild=test_guild)
            logger.info(f"已向测试服务器同步 {len(synced)} 条应用命令。")
        else:
            logger.warning(
                "未设置 TEST_GUILD_ID，将进行全局命令同步（可能需要长达一小时生效）。"
            )
            logger.info("正在同步全局应用命令...")
            synced = await self.tree.sync()
            logger.info(f"已全局同步 {len(synced)} 条应用命令。")

    async def on_ready(self):
        """当 Bot 完全准备就绪时调用。"""
        logger.info("Bot 已完全准备就绪。")


# --- 应用程序主入口 ---
async def main():
    """应用程序的异步主函数。"""
    if not DISCORD_BOT_TOKEN:
        logger.critical("致命错误：DISCORD_BOT_TOKEN 未在 .env 文件中设置！")
        return

    bot = OdysseiaProtect()

    logger.info("Bot 正在启动...")
    # 使用 atexit 来确保即使发生意外错误，也能尝试关闭
    try:
        await bot.start(DISCORD_BOT_TOKEN)
    finally:
        if not bot.is_closed():
            logger.info("正在关闭 Bot 会话...")
            await bot.close()
            logger.info("Bot 会话已成功关闭。")


# --- 运行主程序 ---
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("检测到键盘中断，程序已终止。")
    except Exception as e:
        logger.critical(f"应用程序顶层出现未捕获的异常: {e}", exc_info=e)
