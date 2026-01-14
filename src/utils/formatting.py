# -*- coding: utf-8 -*-
"""
格式化工具函数。
"""

from typing import Sequence, Union
import discord

from src.database.models import Resource

def format_resource_list_chunks(
    resource_list: Sequence[Resource],
    *,
    is_normal_mode: bool = False,
    show_download_count: bool = True,
    source: Union[discord.Interaction, discord.Message],
) -> list[str]:
    """将资源列表切分为多个不超过 1024 字符的块"""
    if not resource_list:
        return ["无"]

    assert source.channel is not None

    chunks = []
    current_chunk = ""
    guild_id = source.guild.id if hasattr(source, "guild") and source.guild else None

    for r in resource_list:
        # 构造单行文字
        v_info = (r.version_info[:30] + "..") if len(r.version_info) > 30 else r.version_info
        f_name = (r.filename[:30] + "..") if r.filename and len(r.filename) > 30 else r.filename
        
        line = f"🔹 **{v_info}** (`{f_name}`)"
        if is_normal_mode and guild_id:
            message_url = f"https://discord.com/channels/{guild_id}/{source.channel.id}/{r.source_message_id}"
            line += f" - [跳转]({message_url})"
        elif not is_normal_mode and show_download_count:
            line += f" - 📥 {r.download_count}"

        # 检查长度 (Discord 限制 1024)
        if len(current_chunk) + len(line) + 2 > 1000:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk = (current_chunk + "\n" + line) if current_chunk else line

    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks
