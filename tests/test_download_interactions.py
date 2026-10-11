"""无密码下载的首次应答与私密心愿单面板刷新回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.dto.rate_limit_config import RateLimitConfig
from src.services.rate_limit_service import RateLimitService
from src.enums.wishlist import WISHLIST_ADDED_MESSAGE
from src.models.resource import Resource
from src.qo.download_page_qo import DownloadPageQo
from src.qo.rate_limit_qo import RateLimitQo
from src.qo.resource_selection_qo import ResourceSelectionQo
from src.utils.safe_defer import safe_defer
from tests.test_download_pagination import seed, service
from tests.test_download_rate_limit import make_bot_interaction, make_dto
from tests.test_download_ui import (
    make_interaction, make_private_view, make_public_view, make_resource, make_session_context,
)


@pytest.mark.asyncio
async def test_download_dtos_do_not_retain_historical_passwords(db_session):
    """历史密码不进入资源列表与下载详情的数据契约。"""
    await seed(db_session, 3)
    for resource_id, password in ((1, "do-not-retain"), (2, None), (3, "")):
        resource = await db_session.get(Resource, resource_id)
        resource.password = password
    await db_session.flush()
    page = await service().get_page(db_session, DownloadPageQo(public_thread_id=300))
    assert "do-not-retain" not in page.model_dump_json()
    assert all(not hasattr(item, "password") and not hasattr(item, "has_password") for item in page.items)
    detail = await service().get_selected_resource(
        db_session, ResourceSelectionQo(public_thread_id=300, resource_id=1),
    )
    assert "password" not in detail.model_dump()
    assert (await db_session.get(Resource, 1)).password == "do-not-retain"


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("list_password,current_password", [
    ("old-secret", "new-secret"), ("old-secret", None), (None, "new-secret"), ("", ""),
])
async def test_selection_defers_and_delivers_regardless_of_password_changes(
    db_session, public, list_password, current_password, monkeypatch, caplog,
):
    """数据库密码变化不阻止旧列表下载，实际交付只扣一次额度。"""
    import logging

    await seed(db_session, 1)
    resource = await db_session.get(Resource, 1)
    resource.password = current_password
    resource.author_note = "**最新备注**\n下载面板读取最新详情"
    await db_session.flush()
    view = (make_public_view if public else make_private_view)(
        [make_resource(1, version="v1", password=list_password)]
    )
    select = view.children[0]
    select._values = ["1"]
    interaction = make_bot_interaction()
    interaction.client.rate_limit_service = RateLimitService(
        RateLimitConfig(daily_watch_threshold=1), redis_url="",
    )
    await interaction.client.rate_limit_service.check(RateLimitQo(123))
    context = make_session_context()
    context.__aenter__.return_value = db_session

    async def selected(session, qo):
        assert interaction.response.is_done()
        return await service().get_selected_resource(session, qo)

    interaction.client.download_service.get_selected_resource = AsyncMock(side_effect=selected)
    monkeypatch.setattr("src.ui.resource_select.AsyncSessionLocal", lambda: context)
    monkeypatch.setattr("src.ui.resource_select_view.AsyncSessionLocal", make_session_context)
    caplog.set_level(logging.INFO)
    await select.callback(interaction)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=public)
    interaction.response.send_modal.assert_not_awaited()
    interaction.client.download_service.fetch_fresh_url.assert_awaited_once()
    detail = interaction.client.download_service.fetch_fresh_url.await_args.args[0]
    interaction.client.dispatch.assert_called_once_with("resource_downloaded", detail)
    panel = interaction.edit_original_response.await_args.kwargs["view"]
    assert panel.selected_resource_id == 1
    result_embed = interaction.edit_original_response.await_args.kwargs["embeds"][0]
    assert resource.author_note in result_embed.description
    assert (await interaction.client.rate_limit_service.check(RateLimitQo(123, charge=False))).daily_count == 2
    logs = [record.getMessage() for record in caplog.records if record.getMessage().startswith("WATCH:")]
    assert len(logs) == 1 and "outcome=success" in logs[0]
    assert "secret" not in logs[0]
    assert resource.password == current_password


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
async def test_historical_password_trace_selection_still_requires_consent(public, monkeypatch):
    """历史有密码的溯源资源直接进入同意流程，等待及拒绝不扣额度。"""
    view = (make_public_view if public else make_private_view)(
        [make_resource(1, version="v1", password="secret")]
    )
    view.children[0]._values = ["1"]
    interaction = make_bot_interaction()
    interaction.client.download_service.get_selected_resource = AsyncMock(
        return_value=make_dto(trace_enabled=True)
    )
    monkeypatch.setattr("src.ui.resource_select.AsyncSessionLocal", make_session_context)
    await view.children[0].callback(interaction)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=public)
    interaction.response.send_modal.assert_not_awaited()
    response = interaction.edit_original_response if public else interaction.followup.send
    consent = response.await_args.kwargs["view"]
    assert consent.resource.trace_enabled
    interaction.client.download_service.fetch_delivery.assert_not_called()
    refusal = make_bot_interaction(bot=interaction.client)
    refusal.response.edit_message = AsyncMock()
    await consent.disagree.callback(refusal)
    interaction.client.download_service.fetch_delivery.assert_not_called()
    assert (await interaction.client.rate_limit_service.check(RateLimitQo(123, charge=False))).daily_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("state", ["finished", "busy", "deleted", "query_error"])
async def test_selection_failure_never_delivers_or_charges(public, state, monkeypatch):
    """移除密码分支后仍拒绝失效面板、已删除资源与查询故障。"""
    view = (make_public_view if public else make_private_view)(
        [make_resource(1, version="v1", password="secret")]
    )
    view.children[0]._values = ["1"]
    interaction = make_bot_interaction()
    query = AsyncMock(return_value=None)
    interaction.client.download_service.get_selected_resource = query
    monkeypatch.setattr("src.ui.resource_select.AsyncSessionLocal", make_session_context)
    if state == "finished":
        view.stop()
    elif state == "busy":
        await view.state_lock.acquire()
    elif state == "query_error":
        query.side_effect = RuntimeError("database unavailable")
    try:
        await view.children[0].callback(interaction)
    finally:
        if state == "busy":
            view.state_lock.release()
    interaction.client.download_service.fetch_fresh_url.assert_not_awaited()
    interaction.client.dispatch.assert_not_called()
    interaction.response.send_modal.assert_not_awaited()
    if state in {"finished", "busy"}:
        query.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
    else:
        response = interaction.edit_original_response if public else interaction.followup.send
        response.assert_awaited_once()
    assert (await interaction.client.rate_limit_service.check(RateLimitQo(123, charge=False))).daily_count == 0


@pytest.mark.asyncio
async def test_safe_defer_skips_existing_response():
    interaction = make_interaction(MagicMock())
    interaction.response.is_done.return_value = True
    await safe_defer(interaction, thinking=True)
    interaction.response.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_defer_only_ignores_already_responded_race(caplog):
    interaction = make_interaction(MagicMock())
    interaction.response.defer.side_effect = discord.InteractionResponded(interaction)
    await safe_defer(interaction)
    assert "竞态" in caplog.text
    for error in (OSError(104, "reset"), discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"), {"code": 10062, "message": "expired"}
    )):
        interaction.response.defer.side_effect = error
        with pytest.raises(type(error)):
            await safe_defer(interaction)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["current", "paged", "finished", "expired", "http", "network"])
async def test_wishlist_consent_edits_original_interaction_after_commit(state, monkeypatch, caplog):
    view = make_private_view([make_resource(1, version="v1")])
    view.selected_resource_id = 1
    view.set_wishlist_state(False)
    service = MagicMock()
    service.add = AsyncMock(return_value="needs_consent")
    service.accept_policy_and_add = AsyncMock(return_value="added")
    origin = make_interaction(MagicMock())
    origin.client.wishlist_service = service
    origin.message = SimpleNamespace(id=991, edit=AsyncMock())
    origin.token = "do-not-log-token"
    session = MagicMock(commit=AsyncMock(), rollback=AsyncMock())
    context = make_session_context()
    context.__aenter__.return_value = session
    monkeypatch.setattr("src.ui.add_to_wishlist_button.AsyncSessionLocal", lambda: context)
    monkeypatch.setattr("src.ui.wishlist_consent_view.AsyncSessionLocal", lambda: context)
    await view.add_button.callback(origin)
    consent = origin.followup.send.await_args.kwargs["view"]
    assert consent.panel_interaction is origin
    if state == "paged":
        view.selected_resource_id = 2
    elif state == "finished":
        view.stop()
    elif state == "expired":
        origin.is_expired.return_value = True

    async def edit_panel(**kwargs):
        session.commit.assert_awaited_once()
        assert kwargs == {"view": view}
        if state == "http":
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"),
                                   {"code": 10008, "message": "gone"})
        if state == "network":
            raise OSError(104, "connection reset")

    origin.edit_original_response.side_effect = edit_panel
    confirmation = make_interaction(MagicMock())
    await consent.agree.callback(confirmation)
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    origin.message.edit.assert_not_awaited()
    if state in {"current", "http", "network"}:
        origin.edit_original_response.assert_awaited_once_with(view=view)
    else:
        origin.edit_original_response.assert_not_awaited()
    if state != "paged" and state != "finished":
        assert view.add_button.disabled and not view.remove_button.disabled
    else:
        assert not view.add_button.disabled and view.remove_button.disabled
    confirmation.edit_original_response.assert_awaited_once_with(
        content=WISHLIST_ADDED_MESSAGE, embed=None, view=None,
    )
    if state == "http":
        assert "status=404 code=10008 message_id=991" in caplog.text
    assert "do-not-log-token" not in caplog.text
