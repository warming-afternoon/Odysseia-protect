"""北京时间文件轮转及日志初始化验收。"""

import logging
from datetime import datetime
from unittest.mock import patch

import pytest

from src.utils.beijing_formatter import BeijingFormatter
from src.utils.beijing_rotating_file_handler import BeijingRotatingFileHandler
from src.utils.beijing_time import BEIJING
from src.utils.logging_setup import setup_logging


def test_rotation_uses_beijing_dates_and_keeps_seven_archives(tmp_path):
    """跨北京时间午夜轮转，归档日期和保留数量正确。"""
    filename = tmp_path / "bot.log"
    handler = BeijingRotatingFileHandler(filename)
    try:
        handler.rolloverAt = datetime(2026, 10, 10, tzinfo=BEIJING).timestamp()
        for day in range(9, 19):
            now = datetime(2026, 10, day, 23, 59, 59, tzinfo=BEIJING).timestamp()
            record = logging.LogRecord(
                "test", logging.INFO, __file__, 1, f"中文日志 {day}", (), None
            )
            with patch(
                "src.utils.beijing_rotating_file_handler.time.time", return_value=now
            ):
                handler.emit(record)
            now += 1
            with patch(
                "src.utils.beijing_rotating_file_handler.time.time", return_value=now
            ):
                handler.emit(record)
        archives = sorted(tmp_path.glob("bot.log.*"))
        assert len(archives) == 7
        assert archives[0].name == "bot.log.2026-10-12"
        assert archives[-1].name == "bot.log.2026-10-18"
        assert "中文日志 18" in archives[-1].read_text(encoding="utf-8")
        assert (
            handler.computeRollover(
                datetime(2026, 10, 9, 16, tzinfo=BEIJING).timestamp()
            )
            == datetime(2026, 10, 10, tzinfo=BEIJING).timestamp()
        )
    finally:
        handler.close()


def test_log_timestamp_includes_beijing_offset():
    """时间输出带北京时间偏移，不使用宿主本地时区。"""
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "", (), None)
    record.created = datetime(2026, 10, 9, tzinfo=BEIJING).timestamp()
    assert BeijingFormatter().formatTime(record) == "2026-10-09T00:00:00.000+08:00"


@pytest.fixture
def restore_logging():
    """测试后关闭新增处理器并恢复全局日志等级。"""
    root = logging.getLogger()
    level = root.level
    yield
    for handler in tuple(root.handlers):
        if getattr(handler, "_odysseia_protect_logging", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def test_setup_is_idempotent_and_preserves_external_handlers(tmp_path, restore_logging):
    """重复初始化仅保留一组处理器，外部捕获处理器仍存在。"""
    root = logging.getLogger()
    external = tuple(root.handlers)
    setup_logging(values={}, project_root=tmp_path)
    setup_logging(values={}, project_root=tmp_path)
    assert (
        len(
            [
                handler
                for handler in root.handlers
                if getattr(handler, "_odysseia_protect_logging", False)
            ]
        )
        == 2
    )
    assert all(handler in root.handlers for handler in external)
    logging.getLogger("test").info("仅记录一次")
    assert (tmp_path / "logs/bot.log").read_text(encoding="utf-8").count(
        "仅记录一次"
    ) == 1


def test_file_setup_failure_keeps_console(tmp_path, restore_logging, caplog):
    """文件系统故障明确警告并保留控制台处理器。"""
    with patch("src.utils.logging_setup.Path.mkdir", side_effect=OSError):
        setup_logging(values={}, project_root=tmp_path)
    managed = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_odysseia_protect_logging", False)
    ]
    assert len(managed) == 1
    assert "仅输出到控制台" in caplog.text


@pytest.mark.parametrize(
    "values", [{"LOG_BACKUP_COUNT": "0"}, {"LOG_BACKUP_COUNT": "bad"}, {"LOG_DIR": " "}]
)
def test_invalid_logging_configuration_fails(values):
    """非法轮转配置在改变处理器之前报错。"""
    with pytest.raises(ValueError):
        setup_logging(values=values)
