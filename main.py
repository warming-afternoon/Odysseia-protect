# -*- coding: utf-8 -*-
"""
Bot 主入口文件。
"""

# --- 导入 ---
import asyncio
import logging
import os
import sys
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
# from src.services.reaction_wall_service import ReactionWallService


# --- 可选的 uvloop 性能加速 ---
# 在非 Windows 系统上，尝试启用 uvloop 以获得更好的性能。
# 如果未安装 uvloop，则会回退到标准的 asyncio 事件循环。
if sys.platform != "win32":
    try:
        import uvloop

        uvloop.install()
        logging.info("检测到非 Windows 环境，已成功启用 uvloop。")
    except ImportError:
        logging.info("未找到 uvloop，将使用默认的 asyncio 事件循环。")
logger = logging.getLogger(__name__)


# --- 环境变量 ---
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# 解析测试服务器 ID（支持逗号分隔，例如：123456789,987654321）
test_guild_env = os.getenv("TEST_GUILD_IDS") or os.getenv("TEST_GUILD_ID") or ""
TEST_GUILDS = []
if test_guild_env:
    for gid in test_guild_env.split(","):
        gid = gid.strip()
        if gid.isdigit():
            TEST_GUILDS.append(int(gid))


# --- Bot 核心类 ---
class OdysseiaProtect(commands.Bot):
    """自定义 Bot 类，用于封装状态和启动逻辑。"""

    def __init__(self):
        # 定义 Bot Intents
        intents = discord.Intents.default()
        intents.message_content = True  # 允许读取消息内容
        intents.members = True  # 允许访问成员列表和事件

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

    #       self.reaction_wall_service = ReactionWallService(
    #           self, resource_repo, thread_repo, user_repo
    #       )

    async def setup_hook(self):
        """在 Bot 登录后执行异步初始化。"""
        if self.user:
            logger.info(f"成功以 {self.user} (ID: {self.user.id}) 的身份登录！")

        # 初始化数据库
        logger.info("正在初始化数据库...")
        await init_db()
        logger.info("数据库初始化完成。")

        # 动态加载 Cogs
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

        # 同步斜杠命令到指定的测试服务器（即时生效）
        if TEST_GUILDS:
            logger.info(f"检测到 {len(TEST_GUILDS)} 个测试服务器，准备同步...")
            for guild_id in TEST_GUILDS:
                try:
                    test_guild = discord.Object(id=guild_id)
                    # 将全局命令复制到该测试服务器
                    self.tree.copy_global_to(guild=test_guild)
                    synced_test = await self.tree.sync(guild=test_guild)
                    logger.info(f"✅ 成功向测试服务器 {guild_id} 同步 {len(synced_test)} 条命令。")
                except discord.Forbidden:
                    logger.warning(f"❌ 无法向测试服务器 {guild_id} 同步：Bot不在该服务器或缺少应用命令权限。")
                except Exception as e:
                    logger.error(f"❌ 向测试服务器 {guild_id} 同步时发生错误: {e}")
        else:
            logger.info("未配置测试服务器，跳过测试服同步步骤。")

        # 全局同步（受 Discord 缓存影响，新服务器可能需要最多一小时生效）
        logger.info("🌐 正在同步全局应用命令 (全局同步可能有缓存延迟)...")
        try:
            synced_global = await self.tree.sync()
            logger.info(f"✅ 已成功全局同步 {len(synced_global)} 条应用命令。")
        except Exception as e:
            logger.error(f"❌ 全局同步命令时发生错误: {e}")

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
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
        )
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("检测到键盘中断，程序已终止。")
    except Exception as e:
        logger.critical(f"应用程序顶层出现未捕获的异常: {e}", exc_info=e)
