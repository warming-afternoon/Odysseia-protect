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
    channel: discord.abc.Messageable | None = None,
) -> bool:
    """
    检查给定用户是否为指定帖子的作者。
    优先通过 Discord 频道元数据判断，如果无法判断则查询数据库。

    参数:
        session: 数据库会话
        public_thread_id: Discord 帖子频道 ID
        user_id: 要检查的 Discord 用户 ID
        thread_repo: 可选的 ThreadRepository 实例
        channel: 可选的 Discord 频道对象

    返回:
        bool: 是否为作者
    """
    # 检查 Discord 原生元数据 (Thread.owner_id)
    if isinstance(channel, discord.Thread) and channel.owner_id is not None:
        # 如果 Discord 明确记录了 owner_id
        if channel.owner_id == user_id:
            return True
        else:
            # owner_id 存在但不匹配，直接拒绝
            return False

    # 备选逻辑：查询数据库记录（当原生元数据不存在时）
    if thread_repo is None:
        thread_repo = ThreadRepository()
        
    thread = await thread_repo.get_by_public_thread_id(
        session, public_thread_id=public_thread_id
    )
    
    if thread is None:
        # 如果数据库没有记录，且上面原生元数据也没匹配成功，
        # 这里返回 True 是为了允许第一个上传的人成为记录上的"作者"
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
    """
    if not interaction.channel:
        await interaction.response.send_message(
            "❌ 错误：无法确定当前频道。", ephemeral=True
        )
        return False

    public_thread_id = interaction.channel.id
    user_id = interaction.user.id

    # 传递 interaction.channel 以便进行原生所有者检查
    is_author = await is_thread_author(
        session,
        public_thread_id=public_thread_id,
        user_id=user_id,
        thread_repo=thread_repo,
        channel=interaction.channel  # type: ignore
    )
    
    if not is_author:
        await interaction.response.send_message(
            "❌ 权限不足：只有本帖的作者才能执行此操作。", ephemeral=True
        )
        return False
        
    return True
