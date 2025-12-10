# -*- coding: utf-8 -*-
"""
反应墙服务，负责处理反应墙相关的业务逻辑。
"""

import logging
from typing import Optional

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Thread
from src.services.base import BaseService

logger = logging.getLogger(__name__)


class ReactionWallService(BaseService):
    """封装了所有与反应墙相关的业务逻辑。"""

    async def verify_user_reaction(
        self,
        *,
        thread: Thread,
        discord_thread: discord.Thread,
        user: discord.User,
    ) -> bool:
        """
        验证用户是否已对帖子的起始消息做出反应。
        如果 thread.reaction_required 为 False，则返回 True。
        如果 thread.reaction_emoji 有值，则检查特定表情；否则检查任意反应。
        """
        if not thread.reaction_required:
            return True

        # 获取起始消息
        try:
            starter_message = discord_thread.starter_message
            if starter_message is None:
                # 如果未缓存，则获取第一条消息
                async for msg in discord_thread.history(limit=1, oldest_first=True):
                    starter_message = msg
                    break
            if starter_message is None:
                raise ValueError("无法找到起始消息")
        except Exception as e:
            logger.error(f"获取帖子起始消息失败: {e}")
            return False

        # 检查用户是否已做出反应
        user_has_reacted = False
        if thread.reaction_emoji:
            # 检查特定表情
            for reaction in starter_message.reactions:
                if str(reaction.emoji) == thread.reaction_emoji:
                    # 检查该用户是否已做出反应
                    try:
                        users = [u async for u in reaction.users() if u.id == user.id]
                        if users:
                            user_has_reacted = True
                            break
                    except discord.Forbidden:
                        pass
        else:
            # 检查任何反应
            for reaction in starter_message.reactions:
                try:
                    users = [u async for u in reaction.users() if u.id == user.id]
                    if users:
                        user_has_reacted = True
                        break
                except discord.Forbidden:
                    pass

        return user_has_reacted

    async def set_reaction_required(
        self,
        session: AsyncSession,
        *,
        thread_id: int,
        required: bool,
    ) -> Optional[Thread]:
        """设置帖子是否需要反应墙。"""
        thread = await self.thread_repo.get(session, id=thread_id)
        if not thread:
            return None
        update_data = {"reaction_required": required}
        updated = await self.thread_repo.update(
            session, db_obj=thread, obj_in=update_data
        )
        return updated

    async def set_reaction_emoji(
        self,
        session: AsyncSession,
        *,
        thread_id: int,
        emoji: Optional[str],
    ) -> Optional[Thread]:
        """设置帖子的自定义反应表情。"""
        thread = await self.thread_repo.get(session, id=thread_id)
        if not thread:
            return None
        update_data = {"reaction_emoji": emoji}
        updated = await self.thread_repo.update(
            session, db_obj=thread, obj_in=update_data
        )
        return updated
