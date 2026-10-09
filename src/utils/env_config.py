"""环境变量校验工具。"""

from typing import Mapping


def positive_int(values: Mapping[str, str], name: str, default: int) -> int:
    """读取正整数配置，非法值明确报错。"""
    # 未配置时使用默认值，配置错误时拒绝静默降级。
    try:
        value = int(values.get(name, str(default)))
    except (ValueError, TypeError) as error:
        raise ValueError(f"{name} 必须是正整数") from error
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value
