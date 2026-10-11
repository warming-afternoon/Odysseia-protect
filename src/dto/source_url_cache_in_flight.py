"""源附件链接并发回源状态。"""

import asyncio
from dataclasses import dataclass, field


@dataclass
class SourceUrlCacheInFlight:
    """记录回源互斥锁、请求引用计数和缓存失效版本。"""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    """同一源消息在进程内的回源互斥锁，用于合并并发请求。"""

    users: int = 0
    """持锁及等待锁的请求总数，归零后可移除此回源状态。"""

    version: int = 0
    """缓存失效时递增的进程内版本号，用于阻止旧回源结果重新写入。"""
