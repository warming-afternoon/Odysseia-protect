# -*- coding: utf-8 -*-
"""
Discord 相关的工具函数。
"""

import re
from typing import Optional, Tuple


def parse_message_link(link: str) -> Optional[Tuple[int, int, int]]:
    """
    使用正则表达式解析 Discord 消息链接。

    链接格式: https://discord.com/channels/GUILD_ID/CHANNEL_ID/MESSAGE_ID
    返回一个包含 (guild_id, channel_id, message_id) 的元组，如果格式不匹配则返回 None。
    """
    match = re.match(r"https://discord\.com/channels/(\d+)/(\d+)/(\d+)", link)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None
