from dataclasses import dataclass

import discord


@dataclass
class WishlistRender:
    """心愿单界面构建结果，与数据库会话生命周期无关。"""

    view: discord.ui.LayoutView
    """由独立分页数据构建的 Discord Components V2 视图。"""

    file: discord.File | None = None
    """链接文本超长时提供的当前页 TXT 附件，否则为空。"""
