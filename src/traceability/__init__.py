"""角色卡被动溯源能力。"""

from .watermark import WatermarkError, inject_watermark, parse_key, verify_watermark

__all__ = ["WatermarkError", "inject_watermark", "parse_key", "verify_watermark"]
