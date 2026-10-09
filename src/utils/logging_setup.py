"""统一初始化控制台日志与北京时间轮转文件日志。"""

import logging
import os
import sys
from pathlib import Path

from src.utils.beijing_formatter import BeijingFormatter
from src.utils.beijing_rotating_file_handler import BeijingRotatingFileHandler
from src.utils.env_config import positive_int


def setup_logging(*, values=None, project_root: Path | None = None):
    """初始化控制台和轮转文件处理器，重复调用不叠加。"""
    # 先验证配置，再替换本项目管理的日志处理器。
    values = os.environ if values is None else values
    backups = positive_int(values, "LOG_BACKUP_COUNT", 7)
    directory = values.get("LOG_DIR", "logs").strip()
    if not directory:
        raise ValueError("LOG_DIR 不能为空")
    directory = Path(directory)
    if not directory.is_absolute():
        root = (
            project_root
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        directory = root / directory
    formatter = BeijingFormatter(
        "%(asctime)s - [%(levelname)s] - %(name)s: %(message)s"
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in tuple(root_logger.handlers):
        if getattr(handler, "_odysseia_protect_logging", False):
            root_logger.removeHandler(handler)
            handler.close()
    # 普通文本流可重设编码，测试捕获流等特殊对象保持原样。
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console._odysseia_protect_logging = True
    root_logger.addHandler(console)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = BeijingRotatingFileHandler(
            directory / "bot.log", backup_count=backups
        )
        handler.setFormatter(formatter)
        handler._odysseia_protect_logging = True
        root_logger.addHandler(handler)
    except OSError:
        logging.getLogger(__name__).warning("无法创建日志文件处理器，仅输出到控制台")
