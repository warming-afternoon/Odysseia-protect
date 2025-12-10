# -*- coding: utf-8 -*-
"""
服务层的基类，提供公共依赖项和辅助方法。
"""

import logging
import os
from typing import Optional, TYPE_CHECKING

from src.database.repositories.resource import ResourceRepository
from src.database.repositories.thread import ThreadRepository
from src.database.repositories.user import UserRepository

if TYPE_CHECKING:
    from main import OdysseiaProtect

logger = logging.getLogger(__name__)


class BaseService:
    """所有服务类的基类，提供公共依赖项和辅助方法。"""

    def __init__(
        self,
        bot: "OdysseiaProtect",
        resource_repo: ResourceRepository,
        thread_repo: ThreadRepository,
        user_repo: UserRepository,
    ):
        """Service 类的构造函数，通过依赖注入传入所需组件。"""
        self.bot = bot
        self.resource_repo = resource_repo
        self.thread_repo = thread_repo
        self.user_repo = user_repo

        # 读取并验证仓库频道 ID
        warehouse_id_str = os.getenv("WAREHOUSE_CHANNEL_ID")
        self.warehouse_channel_id: Optional[int] = None
        if warehouse_id_str:
            try:
                self.warehouse_channel_id = int(warehouse_id_str)
            except ValueError:
                logger.error("环境变量 WAREHOUSE_CHANNEL_ID 格式无效，必须是纯数字。")
        if not self.warehouse_channel_id:
            logger.warning("WAREHOUSE_CHANNEL_ID 未设置，'受保护文件' 功能将不可用。")
