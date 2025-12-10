"""
鉴权工具函数。
"""

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.repositories.thread import ThreadRepository


async def is_thread_author(
    session: AsyncSession,
    *,
    public_thread_id: int,
    user_id: int,
    thread_repo: ThreadRepository | None = None,
) -> bool:
    """
    检查给定用户是否为指定帖子的作者。

    参数:
        session: 数据库会话
        public_thread_id: Discord 帖子频道 ID
        user_id: 要检查的 Discord 用户 ID
        thread_repo: 可选的 ThreadRepository 实例（如果未提供，将创建一个）

    返回:
        如果帖子不存在，返回 True（因为用户将成为作者）
        如果帖子存在且 author_id 匹配，返回 True
        否则返回 False
    """
    if thread_repo is None:
        thread_repo = ThreadRepository()
    thread = await thread_repo.get_by_public_thread_id(
        session, public_thread_id=public_thread_id
    )
    if thread is None:
        # 帖子不存在，用户将成为作者（允许）
        return True
    return thread.author_id == user_id


async def assert_thread_author(
    session: AsyncSession,
    *,
    interaction: discord.Interaction,
    thread_repo: ThreadRepository | None = None,
) -> bool:
    """
    检查交互用户是否为当前频道的帖子作者。
    如果用户不是作者，则发送错误响应并返回 False。
    如果用户是作者（或帖子不存在），则返回 True。

    注意：此函数假设 interaction.channel 是一个帖子或文本频道。
    """
    if not interaction.channel:
        await interaction.response.send_message(
            "❌ 错误：无法确定当前频道。", ephemeral=True
        )
        return False

    public_thread_id = interaction.channel.id
    user_id = interaction.user.id

    is_author = await is_thread_author(
        session,
        public_thread_id=public_thread_id,
        user_id=user_id,
        thread_repo=thread_repo,
    )
    if not is_author:
        # 帖子存在且用户不是作者
        await interaction.response.send_message(
            "❌ 权限不足：只有本帖的作者才能执行此操作。", ephemeral=True
        )
        return False
    return True
