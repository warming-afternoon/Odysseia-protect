import logging
from datetime import datetime

from src.utils.beijing_time import BEIJING


class BeijingFormatter(logging.Formatter):
    """以北京时间输出日志时间的格式化器。"""

    def formatTime(self, record, datefmt=None):
        """将日志时间格式化为北京时间。"""
        # 输出明确的时区偏移，便于跨环境核对事件时间。
        stamp = datetime.fromtimestamp(record.created, BEIJING)
        return (
            stamp.strftime(datefmt)
            if datefmt
            else stamp.isoformat(timespec="milliseconds")
        )
