"""向 StellariaPact 转发有效讨论消息事件。"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import aiohttp
import discord
import regex as re
from discord.ext import commands

if TYPE_CHECKING:
    from main import OdysseiaProtect

logger = logging.getLogger(__name__)

EVENT_PATH = "/api/v1/message-events"
REQUEST_TIMEOUT_SECONDS = 5
BULK_DELETE_CONCURRENCY = 5


class StellariaForwarderCog(commands.Cog):
    """将有效讨论消息转发给 StellariaPact。"""

    def __init__(self, bot: OdysseiaProtect):
        """初始化消息转发 Cog。"""
        # 读取转发开关和目标接口配置。
        self.bot = bot
        self.enabled = self._is_enabled(
            os.getenv("STELLARIA_FORWARDER_ENABLED", "false")
        )
        self.forum_id = self._parse_forum_id(
            os.getenv("STELLARIA_DISCUSSION_FORUM_ID", "")
        )
        self.base_url = (
            os.getenv("STELLARIA_EVENT_API_BASE_URL", "").strip().rstrip("/")
        )
        self.token = os.getenv("STELLARIA_EVENT_API_TOKEN", "").strip()

        # 初始化共享会话、并发限制和消息有效性规则。
        self._session: aiohttp.ClientSession | None = None
        self._send_semaphore = asyncio.Semaphore(BULK_DELETE_CONCURRENCY)
        self.emoji_pattern = re.compile(
            r"^(<a?:\w+:\d+>|\p{Emoji_Presentation}|\p{Emoji_Modifier_Base}|"
            r"\p{Emoji_Component}|\p{So}|\p{Cn})+$"
        )

    @staticmethod
    def _is_enabled(value: str) -> bool:
        """解析环境变量中的功能开关。"""
        # 兼容常用的真值写法。
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _parse_forum_id(value: str) -> int | None:
        """解析并校验论坛频道 ID。"""
        # 非数字配置视为无效配置。
        value = value.strip()
        if not value.isdigit():
            return None

        # Discord ID 必须是正整数。
        forum_id = int(value)
        return forum_id if forum_id > 0 else None

    def _configuration_errors(self) -> list[str]:
        """收集阻止转发功能启动的配置错误。"""
        # 分别校验论坛 ID、接口地址和共享令牌。
        errors: list[str] = []
        if self.forum_id is None:
            errors.append("STELLARIA_DISCUSSION_FORUM_ID must be a positive integer")

        parsed_url = urlparse(self.base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            errors.append("STELLARIA_EVENT_API_BASE_URL must be an HTTP(S) URL")

        if not self.token:
            errors.append("STELLARIA_EVENT_API_TOKEN is required")
        return errors

    async def cog_load(self) -> None:
        """加载 Cog 时验证配置并创建共享 HTTP 会话。"""
        # 功能关闭时不创建任何网络资源。
        if not self.enabled:
            logger.info("Stellaria message forwarding is disabled.")
            return

        # 配置不完整时拒绝启用转发功能。
        errors = self._configuration_errors()
        if errors:
            self.enabled = False
            logger.error(
                "Stellaria message forwarding was not enabled: %s",
                "; ".join(errors),
            )
            return

        # 所有消息复用同一个带超时限制的 HTTP 会话。
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        )
        logger.info(
            "Stellaria message forwarding enabled for forum %s -> %s%s",
            self.forum_id,
            self.base_url,
            EVENT_PATH,
        )

    async def cog_unload(self) -> None:
        """卸载 Cog 时释放共享 HTTP 会话。"""
        # 只关闭仍处于打开状态的会话。
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def is_valid_message(self, message: discord.Message) -> bool:
        """判断消息是否满足投票资格的有效发言规则。"""
        # 忽略空消息和机器人消息。
        content = message.content.strip()
        if not content or message.author.bot:
            return False

        # 移除空白后排除纯表情内容。
        content_without_whitespace = re.sub(r"\s", "", content)
        if self.emoji_pattern.match(content_without_whitespace):
            return False

        # 有效发言必须超过四个非空白字符。
        return len(content_without_whitespace) > 4

    def _is_target_message(self, message: discord.Message) -> bool:
        """判断消息是否来自配置的目标论坛帖子。"""
        # 同时校验开关、帖子类型、父论坛和服务器上下文。
        return (
            self.enabled
            and self.forum_id is not None
            and isinstance(message.channel, discord.Thread)
            and message.channel.parent_id == self.forum_id
            and message.guild is not None
        )

    async def _forward_event(
        self,
        event_type: Literal["message_created", "message_deleted"],
        message: discord.Message,
    ) -> None:
        """将单个消息事件发送到 StellariaPact。"""
        # 会话不可用时记录错误并放弃当前事件。
        if self._session is None or self._session.closed:
            logger.error(
                "Cannot forward %s for message %s: HTTP session is unavailable.",
                event_type,
                message.id,
            )
            return

        # 再次收窄 Discord 类型，保证构造事件时字段完整。
        channel = message.channel
        if not isinstance(channel, discord.Thread) or message.guild is None:
            return

        # 仅传输资格处理所需的 Discord ID 元数据。
        payload = {
            "schema_version": 1,
            "event_type": event_type,
            "message_id": str(message.id),
            "guild_id": str(message.guild.id),
            "forum_id": str(channel.parent_id),
            "thread_id": str(channel.id),
            "user_id": str(message.author.id),
        }
        url = f"{self.base_url}{EVENT_PATH}"
        headers = {"Authorization": f"Bearer {self.token}"}

        # 使用信号量限制批量删除产生的并发请求数量。
        async with self._send_semaphore:
            try:
                async with self._session.post(
                    url, json=payload, headers=headers
                ) as response:
                    # 任意成功状态均视为事件已经处理。
                    if 200 <= response.status < 300:
                        logger.debug(
                            "Forwarded %s for message %s.", event_type, message.id
                        )
                        return

                    # 截断响应内容，避免错误日志无限增长。
                    response_text = (await response.text())[:500]
                    logger.error(
                        "Stellaria API rejected %s for message %s with HTTP %s: %s",
                        event_type,
                        message.id,
                        response.status,
                        response_text,
                    )
            except (aiohttp.ClientError, TimeoutError):
                # 简化方案明确不重试，失败事件只写入日志。
                logger.exception(
                    "Failed to forward %s for message %s; the event will not be retried.",
                    event_type,
                    message.id,
                )

    async def _handle_message(
        self,
        event_type: Literal["message_created", "message_deleted"],
        message: discord.Message,
    ) -> None:
        """过滤消息并转发符合条件的事件。"""
        # 非目标论坛或无效发言不会产生网络请求。
        if not self._is_target_message(message) or not self.is_valid_message(message):
            return

        # 将已通过过滤的事件交给统一发送方法。
        await self._forward_event(event_type, message)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """监听新消息并转发有效发言创建事件。"""
        # 新消息对应资格计数增加事件。
        await self._handle_message("message_created", message)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        """监听缓存内单条删除并转发删除事件。"""
        # 缓存内删除消息包含重新校验资格所需的正文和作者。
        await self._handle_message("message_deleted", message)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        """并发处理缓存内的批量删除消息。"""
        # gather 配合发送信号量避免串行请求和无界并发。
        await asyncio.gather(
            *(self._handle_message("message_deleted", message) for message in messages)
        )


async def setup(bot: OdysseiaProtect) -> None:
    """将 Stellaria 消息转发 Cog 注册到下载 Bot。"""
    # 交由 discord.py 管理 Cog 的加载与卸载生命周期。
    await bot.add_cog(StellariaForwarderCog(bot))
