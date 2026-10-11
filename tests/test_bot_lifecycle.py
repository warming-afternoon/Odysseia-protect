"""机器人关闭顺序及异常清理行为。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from main import OdysseiaProtect


@pytest.mark.asyncio
@pytest.mark.parametrize('failures', [
    (), ('verification',), ('delivery',), ('source_url_cache',),
    ('rate_limit',), ('discord',), ('verification', 'source_url_cache', 'discord'),
])
async def test_close_releases_all_services_in_order_after_failures(failures):
    """确认关闭顺序稳定，服务清理失败后仍关闭其余服务和 Discord 会话。"""
    # 跳过初始化，使用模拟关闭操作验证清理流程而不建立外部连接。
    bot = object.__new__(OdysseiaProtect)
    calls = Mock()
    names = ('verification', 'delivery', 'source_url_cache', 'rate_limit', 'discord')
    closers = {}
    for name in names:
        closer = AsyncMock()
        if name in failures:
            closer.side_effect = RuntimeError(name)
        calls.attach_mock(closer, name)
        closers[name] = closer
        if name != 'discord':
            setattr(bot, f'{name}_service', SimpleNamespace(close=closer))
    # 与嵌套 finally 一致，最后发生的清理异常在全部关闭尝试后传播。
    with patch('main.commands.Bot.close', closers['discord']):
        if failures:
            with pytest.raises(RuntimeError) as error:
                await bot.close()
            assert str(error.value) == failures[-1]
        else:
            await bot.close()
    assert calls.mock_calls == [getattr(call, name)() for name in names]
