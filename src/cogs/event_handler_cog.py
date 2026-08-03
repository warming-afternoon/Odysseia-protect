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
from src.enums import SourceStatus

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
        self.thread_repo = ThreadRepository()

    async def _update_source_metadata(
        self,
        *,
        public_thread_id: int,
        guild_id: int | None = None,
        public_thread_name: str | None = None,
        source_status: SourceStatus | None = None,
    ) -> None:
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    await self.thread_repo.update_source_metadata(
                        session,
                        public_thread_id=public_thread_id,
                        guild_id=guild_id,
                        public_thread_name=public_thread_name,
                        source_status=source_status,
                    )
        except Exception:
            logger.exception(
                "同步来源帖子 %s 的元数据失败。",
                public_thread_id,
            )

    @commands.Cog.listener()
    async def on_raw_thread_update(
        self,
        payload: discord.RawThreadUpdateEvent,
    ):
        name = payload.data.get("name")
        if not isinstance(name, str) or not name:
            return
        await self._update_source_metadata(
            public_thread_id=payload.thread_id,
            guild_id=payload.guild_id,
            public_thread_name=name,
            source_status=SourceStatus.ACTIVE,
        )

    @commands.Cog.listener()
    async def on_raw_thread_delete(
        self,
        payload: discord.RawThreadDeleteEvent,
    ):
        await self._update_source_metadata(
            public_thread_id=payload.thread_id,
            guild_id=payload.guild_id,
            source_status=SourceStatus.DELETED,
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ):
        if before.name == after.name:
            return
        await self._update_source_metadata(
            public_thread_id=after.id,
            guild_id=after.guild.id,
            public_thread_name=after.name,
            source_status=SourceStatus.ACTIVE,
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await self._update_source_metadata(
            public_thread_id=channel.id,
            guild_id=channel.guild.id,
            source_status=SourceStatus.DELETED,
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
