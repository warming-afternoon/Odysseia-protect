# -*- coding: utf-8 -*-
"""
下载功能的 UI 组件 (View 和 Modal)
"""

import logging
from typing import Sequence

import discord

from src.database.models import Resource
from src.ui.resource_select import ResourceSelect

logger = logging.getLogger(__name__)


class ResourceSelectView(discord.ui.View):
    """
    一个包含版本选择下拉菜单的交互式视图。
    """

    def __init__(self, resources: Sequence[Resource]):
        # 将 timeout 设置为 4 小时 (4 * 60 * 60 = 14400 秒)
        super().__init__(timeout=14400.0)

        self.add_item(ResourceSelect(resources))

    async def on_timeout(self):
        """
        超时后自动触发的清理逻辑。
        """
        # 停止监听，释放该 View 占用的内存
        self.stop()
