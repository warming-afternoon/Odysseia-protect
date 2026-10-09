from src.dto.rate_limit_result import RateLimitResult


class RateLimitExceeded(Exception):
    """可由交互入口直接处理的交付额度拒绝异常。"""

    def __init__(self, result: RateLimitResult):
        """初始化配置和运行状态。"""
        # 保存配置与生命周期状态。
        self.result = result
        super().__init__(self.message)

    @property
    def message(self) -> str:
        """根据命中限制生成可直接发送给用户的提示。"""
        # 隐藏具体额度，保留重置时间和剩余等待秒数。
        if "daily_rate_limit" in self.result.reasons:
            return f"⏳ 今日资源获取已达上限，请在北京时间次日零点后重试（约 {self.result.reset_after} 秒）。"
        return f"⏳ 资源获取过于频繁，请等待 {self.result.reset_after} 秒后重试。"
