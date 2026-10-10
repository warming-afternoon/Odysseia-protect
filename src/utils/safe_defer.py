"""及时延迟应答，避免对同一次交互重复响应。"""

import logging

import discord

logger = logging.getLogger(__name__)


async def safe_defer(
    interaction: discord.Interaction, *, ephemeral: bool = True, thinking: bool = False
) -> None:
    """仅忽略重复应答竞态；HTTP 和网络异常交给调用方处理。"""
    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        except discord.InteractionResponded:
            logger.warning("safe_defer: 交互 %s 在竞态条件下已被响应。", interaction.id)
