# -*- coding: utf-8 -*-
import glob
import os
import gzip
import shutil
import sqlite3
import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from discord.ext import commands, tasks
import aioboto3

from src.enums.path import DB_PATH, DB_DIR

if TYPE_CHECKING:
    from main import OdysseiaProtect

logger = logging.getLogger(__name__)

TEMP_BACKUP_PATTERN = f"{DB_DIR}/backup_temp_*.db"


class BackupCog(commands.Cog):
    """定时将 SQLite 数据库安全快照压缩后上传至 S3 兼容对象存储（如 Cloudflare R2）"""

    def __init__(self, bot: "OdysseiaProtect"):
        self.bot = bot
        self.backup_enabled = os.getenv("BACKUP_ENABLED", "false").lower() == "true"

        if self.backup_enabled:
            missing = [
                k for k in [
                    "BACKUP_ENDPOINT_URL",
                    "BACKUP_ACCESS_KEY",
                    "BACKUP_SECRET_KEY",
                    "BACKUP_BUCKET_NAME",
                ] if not os.getenv(k)
            ]
            if missing:
                logger.error(
                    "数据库备份已启用，但缺少必要的环境变量: %s。备份功能已禁用。",
                    ", ".join(missing),
                )
                self.backup_enabled = False

        self._cleanup_stale_temp_files()

        if self.backup_enabled:
            self.backup_task.start()
        else:
            logger.warning("数据库备份功能未启用。")

    @staticmethod
    def _cleanup_stale_temp_files() -> None:
        for stale in glob.glob(TEMP_BACKUP_PATTERN):
            try:
                os.remove(stale)
                logger.info(f"已清理残留临时文件: {stale}")
            except OSError:
                pass

    def cog_unload(self) -> None:
        if self.backup_task.is_running():
            self.backup_task.cancel()

    def _create_compressed_backup_sync(self) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        temp_db_path = f"{DB_DIR}/backup_temp_{timestamp}.db"
        gz_path = f"{DB_DIR}/db_backup_{timestamp}.db.gz"

        try:
            with sqlite3.connect(DB_PATH) as src, sqlite3.connect(temp_db_path) as dst:
                src.backup(dst)

            with open(temp_db_path, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
                shutil.copyfileobj(f_in, f_out)

            return gz_path
        finally:
            if os.path.exists(temp_db_path):
                os.remove(temp_db_path)

    async def _upload_to_s3(self, file_path: str) -> None:
        session = aioboto3.Session()

        client_kwargs = {
            "service_name": "s3",
            "endpoint_url": os.getenv("BACKUP_ENDPOINT_URL"),
            "aws_access_key_id": os.getenv("BACKUP_ACCESS_KEY"),
            "aws_secret_access_key": os.getenv("BACKUP_SECRET_KEY"),
        }
        region = os.getenv("BACKUP_REGION_NAME", "auto")
        if region:
            client_kwargs["region_name"] = region

        file_name = os.path.basename(file_path)
        bucket = os.getenv("BACKUP_BUCKET_NAME")

        async with session.client(**client_kwargs) as client:  # type: ignore
            logger.info(f"正在上传备份 {file_name} 到存储桶 {bucket} ...")

            await client.upload_file(
                Filename=file_path,
                Bucket=bucket,
                Key=f"odysseia_backups/{file_name}",
            )

            logger.info(f"备份上传成功: {file_name}")

    @tasks.loop(hours=2.0)
    async def backup_task(self) -> None:
        logger.info("开始执行定期数据库备份任务...")
        gz_path = None
        try:
            gz_path = await asyncio.to_thread(self._create_compressed_backup_sync)
            await self._upload_to_s3(gz_path)
        except Exception as e:
            logger.error(f"数据库备份失败: {e}\n{traceback.format_exc()}")
        finally:
            if gz_path and os.path.exists(gz_path):
                os.remove(gz_path)

    @backup_task.before_loop
    async def before_backup_task(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: "OdysseiaProtect"):
    await bot.add_cog(BackupCog(bot))
