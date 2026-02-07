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
        super().__init__(timeout=None)

        self.add_item(ResourceSelect(resources))
