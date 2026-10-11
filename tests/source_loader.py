"""源附件链接回源测试的模拟加载器。"""

import asyncio
from dataclasses import dataclass


@dataclass
class SourceLoader:
    """可记录调用次数并控制并发顺序的模拟源链接加载器。"""

    result: str
    entered: asyncio.Event | None = None
    resume: asyncio.Event | None = None
    delay: float = 0
    calls: int = 0

    async def __call__(self) -> str:
        """按测试设置暂停回源并记录实际调用次数。"""
        # 调用计数用于验证同一源消息的并发请求只执行一次回源。
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        # 等待失效事件或模拟网络延迟，让测试能覆盖持锁期间的竞争。
        if self.resume is not None:
            await self.resume.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result
