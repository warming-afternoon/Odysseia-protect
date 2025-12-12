# -*- coding: utf-8 -*-
"""
格式化工具函数。
"""

from typing import Sequence, Union
import discord

from src.database.models import Resource


def format_resource_list(
    resource_list: Sequence[Resource],
    *,
    is_normal_mode: bool = False,
    show_download_count: bool = True,
    source: Union[discord.Interaction, discord.Message],
) -> str:
    """将资源列表格式化为 Embed 字段值。"""
    if not resource_list:
        return "无"
    lines = []
    # 断言 interaction.channel 是存在的，因为上层已经校验过
    assert source.channel is not None

    for r in resource_list[:10]:  # 每个字段最多显示10个
        line = f"🔹 **{r.version_info or '未命名'}** (`{r.filename}`)"
        if is_normal_mode:
            # 为普通资源生成跳转链接
            # 我们假设 guild_id 总是存在，因为这些命令是仅限服务器的
            guild_id = None
            if isinstance(source, discord.Interaction):
                guild_id = source.guild_id
            elif isinstance(source, discord.Message) and source.guild:
                guild_id = source.guild.id

            if guild_id:
                message_url = f"https://discord.com/channels/{guild_id}/{source.channel.id}/{r.source_message_id}"
                line += f" - [跳转到消息]({message_url})"
        else:
            # 只为受保护资源显示下载次数
            if show_download_count:
                line += f" - 📥 下载 {r.download_count} 次"
        lines.append(line)

    if len(resource_list) > 10:
        lines.append("...")
    return "\n".join(lines)
