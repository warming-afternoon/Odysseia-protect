# -*- coding: utf-8 -*-
"""
处理和响应由其他机器人组件分发的内部事件的 Cog。
"""

import logging

import discord
from discord.ext import commands
from typing import TYPE_CHECKING


from src.database.database import AsyncSessionLocal
from src.dto.resource_dto import ResourceDTO
from src.database.repositories.resource import ResourceRepository
from src.database.repositories.thread import ThreadRepository
from src.database.repositories.user import UserRepository
from src.services.download_service import DownloadService

if TYPE_CHECKING:
    from main import OdysseiaProtect

logger = logging.getLogger(__name__)


class EventHandlerCog(commands.Cog):
    """一个用于处理内部事件的监听器 Cog"""

    def __init__(self, bot: "OdysseiaProtect"):
        self.bot = bot
        self.download_service = DownloadService(
            bot=bot,
            resource_repo=ResourceRepository(),
            thread_repo=ThreadRepository(),
            user_repo=UserRepository(),
        )

    @commands.Cog.listener()
    async def on_resource_downloaded(self, resource: ResourceDTO):
        """
        监听 on_resource_downloaded 事件，并增加下载计数。
        """
        logger.info(f"接收到 resource_downloaded 事件，资源 ID: {resource.id}")

        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # 调用服务层方法来处理业务逻辑
                    await self.download_service.increment_download_count(
                        session, resource_id=resource.id
                    )
        except Exception as e:
            logger.error(
                f"在 on_resource_downloaded 事件处理中更新资源 {resource.id} 的下载计数失败。",
                exc_info=e,
            )


async def setup(bot: "OdysseiaProtect"):
    """将 Cog 添加到机器人中。"""
    await bot.add_cog(EventHandlerCog(bot))
