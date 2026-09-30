"""作者解析、资源维护及管理组鉴权。"""

import os
import re

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Thread
from src.database.repositories.collaborator import CollaboratorRepository
from src.database.repositories.thread import ThreadRepository

MANAGEMENT_DENIED = "权限不足：只有本帖原作者或其协作者才能维护资源。"


def parse_user_id(value: str) -> int:
    match = re.fullmatch(r"(?:<@!?([0-9]{1,19})>|([0-9]{1,19}))", value.strip())
    if match is None:
        raise ValueError("请提供用户提及或有效的数字用户 ID。")
    user_id = int(match.group(1) or match.group(2))
    if not 0 < user_id <= 2**63 - 1:
        raise ValueError("用户 ID 超出有效范围。")
    return user_id


def _id_set(name: str) -> set[int]:
    return {int(value.strip()) for value in os.getenv(name, "").split(",")
            if value.strip().isascii() and value.strip().isdigit()}


def is_management_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    if interaction.user.id in _id_set("TRACE_ADMIN_USER_IDS"):
        return True
    allowed_roles = _id_set("TRACE_ADMIN_ROLE_IDS")
    return any(role.id in allowed_roles for role in getattr(interaction.user, "roles", ()))


def can_edit_collaborators(interaction: discord.Interaction, author_id: int) -> bool:
    return interaction.guild is not None and (
        interaction.user.id == author_id or is_management_admin(interaction)
    )


async def resolve_thread_author(
    session: AsyncSession, *, public_thread_id: int,
    channel: discord.abc.Messageable | None = None,
    thread_repo: ThreadRepository | None = None,
    thread_model: Thread | None = None,
) -> int | None:
    if isinstance(channel, discord.Thread) and channel.id == public_thread_id:
        owner_id = channel.owner_id
        if isinstance(owner_id, int) and owner_id > 0:
            return owner_id
    if thread_model is None:
        thread_model = await (thread_repo or ThreadRepository()).get_by_public_thread_id(
            session, public_thread_id=public_thread_id,
        )
    if thread_model is not None and thread_model.public_thread_id == public_thread_id:
        return thread_model.author_id
    return None


async def is_thread_author(session: AsyncSession, *, public_thread_id: int,
                           user_id: int, thread_repo=None, channel=None) -> bool:
    return user_id == await resolve_thread_author(
        session, public_thread_id=public_thread_id, thread_repo=thread_repo, channel=channel,
    )


async def require_thread_manager(
    session: AsyncSession, *, interaction: discord.Interaction,
    thread_repo: ThreadRepository | None = None, thread_model: Thread | None = None,
) -> int:
    channel = interaction.channel
    if interaction.guild is None or not isinstance(channel, (discord.Thread, discord.TextChannel)):
        raise PermissionError("无法确定服务器或当前帖子。")
    if thread_model is not None and (
        thread_model.public_thread_id != channel.id
        or thread_model.guild_id not in (None, interaction.guild.id)
    ):
        raise PermissionError("只能在资源所属的原帖中进行操作。")
    author_id = await resolve_thread_author(
        session, public_thread_id=channel.id, channel=channel,
        thread_repo=thread_repo, thread_model=thread_model,
    )
    if author_id is None:
        raise PermissionError("无法确认原帖主，请稍后重试或联系管理组。")
    if interaction.user.id != author_id and not await CollaboratorRepository().exists(
        session, author_id=author_id, user_id=interaction.user.id,
    ):
        raise PermissionError(MANAGEMENT_DENIED)
    return author_id


async def send_private_error(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(f"❌ {message}", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ {message}", ephemeral=True)


async def assert_thread_manager(session: AsyncSession, *, interaction: discord.Interaction,
                                thread_repo=None) -> bool:
    try:
        await require_thread_manager(session, interaction=interaction, thread_repo=thread_repo)
    except PermissionError as exc:
        await send_private_error(interaction, str(exc))
        return False
    return True


async def assert_thread_author(session: AsyncSession, *, interaction: discord.Interaction,
                               thread_repo=None) -> bool:
    """保留严格的作者检查；资源维护入口使用 assert_thread_manager。"""
    if not interaction.channel:
        await send_private_error(interaction, "错误：无法确定当前频道。")
        return False
    if not await is_thread_author(
        session, public_thread_id=interaction.channel.id, user_id=interaction.user.id,
        thread_repo=thread_repo, channel=interaction.channel,
    ):
        await send_private_error(interaction, "权限不足：只有本帖的作者才能执行此操作。")
        return False
    return True
