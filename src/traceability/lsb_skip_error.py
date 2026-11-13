"""允许保留其他水印层继续交付的像素处理限制。"""

from .watermark import WatermarkError


class LSBSkipError(WatermarkError):
    """携带可审计原因的像素层跳过结果，不表示源文件损坏。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
