import os
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

from src.utils.beijing_time import BEIJING, day_window


class BeijingRotatingFileHandler(TimedRotatingFileHandler):
    """按北京时间午夜归档日志的文件处理器。"""

    def __init__(self, filename, *, backup_count=7):
        """初始化配置和运行状态。"""
        # 保存配置与生命周期状态。
        super().__init__(
            filename,
            when="midnight",
            backupCount=backup_count,
            encoding="utf-8",
            utc=True,
        )

    def computeRollover(self, currentTime):
        """计算下一次北京时间午夜轮转时间。"""
        # 所有部署环境统一在北京时间零点轮转。
        return int(day_window(currentTime)[1])

    def doRollover(self):
        """按北京时间日期归档并清理超出保留数量的文件。"""
        # 归档日期取窗口结束前一秒，确保文件名对应日志所属自然日。
        if self.stream:
            self.stream.close()
            self.stream = None
        date = datetime.fromtimestamp(self.rolloverAt - 1, BEIJING).strftime("%Y-%m-%d")
        destination = self.rotation_filename(f"{self.baseFilename}.{date}")
        if os.path.exists(destination):
            os.remove(destination)
        self.rotate(self.baseFilename, destination)
        for filename in self.getFilesToDelete():
            os.remove(filename)
        if not self.delay:
            self.stream = self._open()
        self.rolloverAt = self.computeRollover(time.time())
