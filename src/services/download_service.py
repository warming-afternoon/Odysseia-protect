"""下载分页、资源详情与文件交付服务。"""

import logging
import math

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from src.dto.download_page_dto import DOWNLOAD_PAGE_SIZE, DownloadPageDTO
from src.dto.download_resource_option_dto import DownloadResourceOptionDTO
from src.dto.resource_dto import ResourceDTO
from src.enums import UploadMode
from src.qo.download_page_qo import DownloadPageQo
from src.qo.resource_selection_qo import ResourceSelectionQo
from src.services.base import BaseService
from src.services.delivery_service import DeliveryResult

logger = logging.getLogger(__name__)


class DownloadService(BaseService):
    """协调帖子与资源数据并提供下载交付。"""

    async def get_page(self, session: AsyncSession, qo: DownloadPageQo) -> DownloadPageDTO:
        """查询资源页并在会话内转换为独立的分页数据。"""
        # 一次查询取得共用的帖子元数据，不访问每个资源的关联。
        thread = await self.thread_repo.get_by_public_thread_id(
            session, public_thread_id=qo.public_thread_id
        )
        if thread is None:
            return DownloadPageDTO(public_thread_id=qo.public_thread_id)

        # 统计总数后校正页码，再通过数据库分页读取当前资源。
        total = await self.resource_repo.count_by_thread_id(session, thread_id=thread.id)
        max_page = max(1, math.ceil(total / DOWNLOAD_PAGE_SIZE))
        page = min(qo.page, max_page)
        resources = await self.resource_repo.get_page_by_thread_id(
            session, thread_id=thread.id,
            offset=(page - 1) * DOWNLOAD_PAGE_SIZE, limit=DOWNLOAD_PAGE_SIZE,
        )
        return DownloadPageDTO(
            public_thread_id=qo.public_thread_id, guild_id=thread.guild_id,
            items=tuple(DownloadResourceOptionDTO.model_validate(r) for r in resources),
            page=page, total=total, max_page=max_page,
        )

    async def get_selected_resource(
        self, session: AsyncSession, qo: ResourceSelectionQo
    ) -> ResourceDTO | None:
        """校验资源所属帖子并在会话内构建下载详情。"""
        # 分别查询两个表，避免依赖会话外的延迟关联加载。
        resource = await self.resource_repo.get(session, id=qo.resource_id)
        if resource is None:
            return None
        thread = await self.thread_repo.get_by_public_thread_id(
            session, public_thread_id=qo.public_thread_id
        )
        if thread is None or resource.thread_id != thread.id:
            return None
        return ResourceDTO(
            id=resource.id, filename=resource.filename, version_info=resource.version_info,
            password=resource.password, source_message_id=resource.source_message_id,
            warehouse_thread_id=thread.warehouse_thread_id,
            public_thread_id=thread.public_thread_id, author_id=thread.author_id,
            guild_id=thread.guild_id, public_thread_name=thread.public_thread_name,
            source_status=thread.source_status, upload_mode=resource.upload_mode,
            trace_enabled=resource.trace_enabled,
        )

    async def increment_download_count(self, session: AsyncSession, resource_id: int):
        """将下载计数事件交给资源仓储处理。"""
        # 保留事件服务接口，由仓储执行单表原子更新。
        if not await self.resource_repo.increment_download_count(session, resource_id=resource_id):
            logger.warning("下载计数资源已不存在：%s", resource_id)

    async def fetch_fresh_url(self, resource: ResourceDTO) -> str:
        """根据源消息动态获取当前有效的 Discord 附件 URL。"""
        if resource.upload_mode == UploadMode.NORMAL:
            channel_id = resource.public_thread_id
        else:
            channel_id = resource.warehouse_thread_id or resource.public_thread_id
        if not channel_id:
            raise ValueError("数据库中未找到该资源关联的频道ID。")

        source_channel = await self.bot.fetch_channel(channel_id)
        if not isinstance(source_channel, (discord.TextChannel, discord.Thread)):
            raise ValueError("资源源频道类型无效。")

        source_message = await source_channel.fetch_message(resource.source_message_id)
        if not source_message.attachments:
            raise ValueError("源消息中没有附件。")
        return source_message.attachments[0].url

    async def fetch_source_bytes(self, resource: ResourceDTO) -> bytes:
        """读取仓库中的源附件，仅供动态溯源缓存未命中时使用。"""
        channel_id = resource.warehouse_thread_id or resource.public_thread_id
        if not channel_id:
            raise ValueError("数据库中未找到该资源关联的频道ID。")
        source_channel = await self.bot.fetch_channel(channel_id)
        if not isinstance(source_channel, (discord.TextChannel, discord.Thread)):
            raise ValueError("资源源频道类型无效。")
        source_message = await source_channel.fetch_message(resource.source_message_id)
        if not source_message.attachments:
            raise ValueError("源消息中没有附件。")
        return await source_message.attachments[0].read()

    async def fetch_delivery(
        self,
        resource: ResourceDTO,
        *,
        user_id: int,
    ) -> DeliveryResult:
        """返回普通附件 URL，或生成/复用动态溯源交付。"""
        if not resource.trace_enabled:
            return DeliveryResult(
                filename=resource.filename or "resource",
                url=await self.fetch_fresh_url(resource),
            )
        delivery_service = getattr(self.bot, "delivery_service", None)
        if delivery_service is None:
            raise RuntimeError("Bot 未配置动态交付服务。")
        return await delivery_service.deliver(
            resource,
            user_id=user_id,
            source_loader=lambda: self.fetch_source_bytes(resource),
        )
