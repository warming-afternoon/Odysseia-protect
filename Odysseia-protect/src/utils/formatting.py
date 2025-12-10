# -*- coding: utf-8 -*-
"""
格式化工具函数。
"""

from typing import Sequence
import discord

from src.database.models import Resource


def format_resource_list(
    resource_list: Sequence[Resource],
    *,
    is_normal_mode: bool = False,
    interaction: discord.Interaction,
) -> str:
    """将资源列表格式化为 Embed 字段值。"""
    if not resource_list:
        return "无"
    lines = []
    # 断言 interaction.channel 是存在的，因为上层已经校验过
    assert interaction.channel is not None

    for r in resource_list[:10]:  # 每个字段最多显示10个
        line = f"🔹 **{r.version_info or '未命名'}** (`{r.filename}`)"
        if is_normal_mode:
            # 为普通资源生成跳转链接
            # 我们假设 interaction.guild_id 总是存在，因为这些命令是仅限服务器的
            message_url = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel.id}/{r.source_message_id}"
            line += f" [跳转到消息]({message_url})"
        lines.append(line)

    if len(resource_list) > 10:
        lines.append("...")
    return "\n".join(lines)
