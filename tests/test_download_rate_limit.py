"""下载和心愿单真实入口的共享额度与拒绝行为验收。"""

import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from src.dto.rate_limit_config import RateLimitConfig
from src.dto.resource_dto import ResourceDTO
from src.exceptions.rate_limit_exceeded import RateLimitExceeded
from src.qo.rate_limit_qo import RateLimitQo
from src.qo.resource_delivery_qo import ResourceDeliveryQo
from src.services.delivery_service import DeliveryResult
from src.services.download_service import request_resource_delivery
from src.services.rate_limit_service import RateLimitService
from src.services.wishlist_service import WishlistService
from src.ui.wishlist_view import WishlistView
from src.utils.download_guard import send_rate_limit_message
from tests.test_download_ui import (
    make_interaction,
    make_private_view,
    make_public_view,
    make_resource,
    make_session_context,
)


def make_bot_interaction(*, bot=None):
    """创建包含真实限流器与可观测交付替身的交互。"""
    download_service = MagicMock()
    download_service.fetch_fresh_url = AsyncMock(
        return_value="https://example.com/private-link"
    )
    interaction = make_interaction(download_service)
    interaction.response.is_done = MagicMock(return_value=True)
    interaction.guild = SimpleNamespace(id=400)
    interaction.channel = SimpleNamespace(id=300)
    if bot is not None:
        interaction.client = bot
    else:
        interaction.client.rate_limit_service = RateLimitService(
            RateLimitConfig(max_requests=1), redis_url=""
        )
    return interaction


def make_dto(*, resource_id=1, password=None, trace_enabled=False):
    """构建可脱离数据库会话使用的测试资源详情。"""
    return ResourceDTO(
        id=resource_id,
        filename="card.png",
        version_info="v1",
        source_message_id=2,
        public_thread_id=300,
        guild_id=400,
        password=password,
        trace_enabled=trace_enabled,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
async def test_selection_denial_does_not_fetch_or_dispatch_and_logs_once(
    public, caplog
):
    """私密和公开资源选择均在交付前拒绝，且只输出一次观察日志。"""
    interaction = make_bot_interaction()
    limiter = interaction.client.rate_limit_service
    await limiter.check(RateLimitQo(123))
    view = (make_public_view if public else make_private_view)(
        [make_resource(1, version="v1")]
    )
    select = view.children[0]
    select._values = ["1"]
    interaction.client.download_service.get_selected_resource = AsyncMock(
        return_value=make_dto()
    )
    caplog.set_level(logging.INFO)
    with patch(
        "src.ui.resource_select.AsyncSessionLocal", return_value=make_session_context()
    ):
        await select.callback(interaction)
    interaction.client.download_service.fetch_fresh_url.assert_not_awaited()
    interaction.client.download_service.fetch_delivery.assert_not_called()
    interaction.client.dispatch.assert_not_called()
    if public:
        assert (
            "过于频繁"
            in interaction.edit_original_response.await_args.kwargs["content"]
        )
    else:
        assert "过于频繁" in interaction.followup.send.await_args.args[0]
    logs = [
        record for record in caplog.records if record.getMessage().startswith("WATCH:")
    ]
    assert len(logs) == 1 and logs[0].levelno == logging.WARNING
    message = logs[0].getMessage()
    assert "resource_id=1" in message and "outcome=denied" in message
    assert "private-link" not in message
    assert (await limiter.check(RateLimitQo(123, charge=False))).daily_count == 1


@pytest.mark.asyncio
async def test_password_selection_wrong_password_and_success_charge_only_once(caplog):
    """选择和错误密码不扣额度，正确密码才在取链前扣一次。"""
    interaction = make_bot_interaction()
    view = make_private_view(
        [make_resource(1, version="v1", password="do-not-log-password")]
    )
    select = view.children[0]
    select._values = ["1"]
    interaction.client.download_service.get_selected_resource = AsyncMock(
        return_value=make_dto(password="do-not-log-password")
    )
    with patch(
        "src.ui.resource_select.AsyncSessionLocal", return_value=make_session_context()
    ):
        await select.callback(interaction)
    modal = interaction.response.send_modal.await_args.args[0]
    limiter = interaction.client.rate_limit_service
    assert (await limiter.check(RateLimitQo(123, charge=False))).daily_count == 0
    wrong = make_bot_interaction(bot=interaction.client)
    modal.password_input._value = "wrong-password"
    await modal.on_submit(wrong)
    assert (await limiter.check(RateLimitQo(123, charge=False))).daily_count == 0
    correct = make_bot_interaction(bot=interaction.client)
    modal.password_input._value = "do-not-log-password"
    await modal.on_submit(correct)
    assert (await limiter.check(RateLimitQo(123, charge=False))).daily_count == 1
    interaction.client.download_service.fetch_fresh_url.assert_awaited_once()
    await limiter.check(RateLimitQo(123))
    caplog.set_level(logging.INFO)
    modal.password_input._value = "wrong-password"
    await modal.on_submit(wrong)
    logs = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("WATCH:")
    ]
    assert len(logs) == 1 and "outcome=incorrect_password" in logs[0]
    assert "do-not-log-password" not in logs[0] and "wrong-password" not in logs[0]


@pytest.mark.asyncio
async def test_trace_consent_charges_only_on_agreement_and_blocks_repeated_delivery():
    """溯源确认前不扣额度，同意后扣一次，重复交付被拒绝。"""
    interaction = make_bot_interaction()
    menu_resource = make_resource(1, version="v1")
    menu_resource.trace_enabled = True
    view = make_private_view([menu_resource])
    select = view.children[0]
    select._values = ["1"]
    interaction.client.download_service.get_selected_resource = AsyncMock(
        return_value=make_dto(trace_enabled=True)
    )
    interaction.client.download_service.fetch_delivery = AsyncMock(
        return_value=DeliveryResult(filename="card.png", file_data=b"personalized-card")
    )
    with patch(
        "src.ui.resource_select.AsyncSessionLocal", return_value=make_session_context()
    ):
        await select.callback(interaction)
    consent = interaction.response.send_message.await_args.kwargs["view"]
    limiter = interaction.client.rate_limit_service
    assert (await limiter.check(RateLimitQo(123, charge=False))).daily_count == 0
    agreed = make_bot_interaction(bot=interaction.client)
    await consent.agree.callback(agreed)
    assert (await limiter.check(RateLimitQo(123, charge=False))).daily_count == 1
    interaction.client.download_service.fetch_delivery.assert_awaited_once()
    await consent.agree.callback(make_bot_interaction(bot=interaction.client))
    interaction.client.download_service.fetch_delivery.assert_awaited_once()


@pytest.mark.asyncio
async def test_failure_after_admission_still_consumes_quota():
    """生成失败不退还额度，后续重试不能重复执行取链。"""
    interaction = make_bot_interaction()
    bot = interaction.client
    bot.download_service.fetch_fresh_url.side_effect = RuntimeError(
        "source unavailable"
    )
    with pytest.raises(RuntimeError):
        await request_resource_delivery(bot, make_dto(), qo=ResourceDeliveryQo(123))
    with pytest.raises(RateLimitExceeded):
        await request_resource_delivery(bot, make_dto(), qo=ResourceDeliveryQo(123))
    bot.download_service.fetch_fresh_url.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_failure_does_not_refund_and_observation_records_failure(caplog):
    """发送失败仍保留交付计数，并记录失败结果。"""
    interaction = make_bot_interaction()
    interaction.client.rate_limit_service = RateLimitService(
        RateLimitConfig(daily_watch_threshold=1), redis_url=""
    )
    await interaction.client.rate_limit_service.check(RateLimitQo(123))
    interaction.client.download_service.get_selected_resource = AsyncMock(
        return_value=make_dto()
    )
    interaction.edit_original_response.side_effect = RuntimeError("send failed")
    view = make_private_view([make_resource(1, version="v1")])
    select = view.children[0]
    select._values = ["1"]
    caplog.set_level(logging.INFO)
    with patch(
        "src.ui.resource_select.AsyncSessionLocal", return_value=make_session_context()
    ):
        await select.callback(interaction)
    assert (
        await interaction.client.rate_limit_service.check(
            RateLimitQo(123, charge=False)
        )
    ).daily_count == 2
    logs = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("WATCH:")
    ]
    assert len(logs) == 1 and "outcome=failed" in logs[0]


@pytest.mark.asyncio
async def test_wishlist_page_charges_once_and_shares_single_resource_budget():
    """整页六项只扣一次，与单资源交付共享额度。"""
    interaction = make_bot_interaction()
    bot = interaction.client
    repository = MagicMock()
    repository.count_for_user = AsyncMock(return_value=6)
    repository.get_page_for_user = AsyncMock(
        return_value=[
            SimpleNamespace(id=index, created_at=datetime(2026, 10, 9))
            for index in range(1, 7)
        ]
    )
    service = WishlistService(bot, MagicMock(), MagicMock(), MagicMock(), repository)
    service._to_resource_dto = MagicMock(
        side_effect=lambda item: make_dto(resource_id=item.id)
    )
    page = await service.get_page(MagicMock(), user_id=123, page=1)
    assert (
        len(page.entries) == 6 and bot.download_service.fetch_fresh_url.await_count == 6
    )
    assert (
        await bot.rate_limit_service.check(RateLimitQo(123, charge=False))
    ).daily_count == 1
    with pytest.raises(RateLimitExceeded):
        await service.get_page(MagicMock(), user_id=123, page=1)
    with pytest.raises(RateLimitExceeded):
        await request_resource_delivery(bot, make_dto(), qo=ResourceDeliveryQo(123))
    assert bot.download_service.fetch_fresh_url.await_count == 6
    repository.count_for_user.return_value = 0
    repository.get_page_for_user.return_value = []
    assert not (await service.get_page(MagicMock(), user_id=123, page=1)).entries
    assert (
        await bot.rate_limit_service.check(RateLimitQo(123, charge=False))
    ).daily_count == 1


@pytest.mark.asyncio
async def test_wishlist_delete_commits_before_limited_refresh_and_removes_old_links():
    """删除先提交，受限刷新清除旧链接而不回滚删除。"""
    interaction = make_bot_interaction()
    limiter = interaction.client.rate_limit_service
    await limiter.check(RateLimitQo(123))
    result = await limiter.check(RateLimitQo(123))
    service = MagicMock()
    service.remove = AsyncMock()
    service.build_render = AsyncMock(side_effect=RateLimitExceeded(result))
    view = WishlistView(service=service, user_id=123, page=1, max_page=1)
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    context = make_session_context()
    context.__aenter__.return_value = session
    timeline = MagicMock()
    timeline.attach_mock(session.commit, "commit")
    timeline.attach_mock(service.build_render, "refresh")
    with patch("src.ui.wishlist_view.AsyncSessionLocal", return_value=context):
        await view._remove_and_refresh(interaction, resource_id=1)
    assert [call[0] for call in timeline.mock_calls] == ["commit", "refresh"]
    session.rollback.assert_not_awaited()
    replacement = interaction.edit_original_response.await_args.kwargs["view"]
    text = "".join(
        item.content
        for item in replacement.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert "删除成功，刷新受限" in text and "https://" not in text
    assert interaction.edit_original_response.await_args.kwargs["attachments"] == []
    assert view.is_finished()


@pytest.mark.asyncio
@pytest.mark.parametrize("responded", [False, True])
async def test_limit_message_handles_unanswered_and_deferred_interactions(responded):
    """尚未响应与已经延迟响应的交互均能收到私密提示。"""
    interaction = make_bot_interaction()
    interaction.response.is_done.return_value = responded
    limiter = interaction.client.rate_limit_service
    await limiter.check(RateLimitQo(123))
    error = RateLimitExceeded(await limiter.check(RateLimitQo(123)))
    await send_rate_limit_message(interaction, error)
    sender = (
        interaction.followup.send if responded else interaction.response.send_message
    )
    sender.assert_awaited_once_with(error.message, ephemeral=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["command", "page", "jump"])
async def test_wishlist_ui_entries_surface_limits_as_private_messages(entry):
    """命令、翻页和跳页均明确处理限流，不替换原有心愿单。"""
    from src.cogs.wishlist_cog import WishlistCog
    from src.ui.wishlist_page_jump_modal import WishlistPageJumpModal

    interaction = make_bot_interaction()
    limiter = interaction.client.rate_limit_service
    await limiter.check(RateLimitQo(123))
    error = RateLimitExceeded(await limiter.check(RateLimitQo(123)))
    service = MagicMock()
    service.build_render = AsyncMock(side_effect=error)
    interaction.client.wishlist_service = service
    if entry == "command":
        cog = WishlistCog(interaction.client)
        with patch(
            "src.cogs.wishlist_cog.AsyncSessionLocal",
            return_value=make_session_context(),
        ):
            await cog._send_wishlist(interaction)
    elif entry == "page":
        view = WishlistView(service=service, user_id=123, page=1, max_page=2)
        with patch(
            "src.ui.wishlist_view.AsyncSessionLocal",
            return_value=make_session_context(),
        ):
            await view._go_to_page(interaction, 2)
    else:
        modal = WishlistPageJumpModal(service=service, user_id=123, max_page=2)
        modal.page_input._value = "2"
        with patch(
            "src.ui.wishlist_page_jump_modal.AsyncSessionLocal",
            return_value=make_session_context(),
        ):
            await modal.on_submit(interaction)
    interaction.followup.send.assert_awaited_once_with(error.message, ephemeral=True)
    interaction.edit_original_response.assert_not_awaited()
