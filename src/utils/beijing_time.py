"""与宿主时区无关的北京时间计算。"""

from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")


def day_window(timestamp: float) -> tuple[str, float]:
    """计算北京时间日期键和次日零点时间戳。"""
    # 显式使用北京时间，不依赖宿主系统的时区。
    now = datetime.fromtimestamp(timestamp, BEIJING)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now.strftime("%Y%m%d"), (midnight + timedelta(days=1)).timestamp()
