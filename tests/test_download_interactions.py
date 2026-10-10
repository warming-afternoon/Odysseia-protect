"""密码交互首次应答与私密心愿单面板刷新回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from pydantic import ValidationError

from src.dto.download_resource_option_dto import DownloadResourceOptionDTO
from src.enums.wishlist import WISHLIST_ADDED_MESSAGE
from src.models.resource import Resource
from src.qo.download_page_qo import DownloadPageQo
from src.qo.rate_limit_qo import RateLimitQo
from src.utils.safe_defer import safe_defer
from tests.test_download_pagination import seed, service
from tests.test_download_rate_limit import make_bot_interaction, make_dto
from tests.test_download_ui import (
    make_interaction, make_private_view, make_public_view, make_resource, make_session_context,
)


@pytest.mark.asyncio
async def test_list_derives_password_flags_without_retaining_passwords(db_session):
    await seed(db_session, 3)
    for resource_id, password in ((1, "do-not-retain"), (2, None), (3, "")):
        resource = await db_session.get(Resource, resource_id)
        resource.password = password
    await db_session.flush()
    page = await service().get_page(db_session, DownloadPageQo(public_thread_id=300))
    assert {item.id: item.has_password for item in page.items} == {1: True, 2: False, 3: False}
    assert "do-not-retain" not in page.model_dump_json()
    assert all(not hasattr(item, "password") for item in page.items)
    fields = page.items[0].model_dump()
    del fields["has_password"]
    with pytest.raises(ValidationError):
        DownloadResourceOptionDTO.model_validate(fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
async def test_password_menu_opens_modal_before_any_database_work(public):
    view = (make_public_view if public else make_private_view)(
        [make_resource(1, version="v1", password="old-secret")]
    )
    select = view.children[0]
    select._values = ["1"]
    interaction = make_bot_interaction()
    interaction.client.download_service.get_selected_resource = AsyncMock()
    with patch("src.ui.resource_select.AsyncSessionLocal") as sessions:
        await select.callback(interaction)
    sessions.assert_not_called()
    interaction.client.download_service.get_selected_resource.assert_not_awaited()
    interaction.response.send_modal.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()
    modal = interaction.response.send_modal.await_args.args[0]
    assert modal.resource_id == 1 and modal.public_thread_id == 300
    assert not hasattr(modal, "resource")
    assert "old-secret" not in repr(select.password_flags)


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("current_password, entered, allowed", [
    ("new-secret", "old-secret", False),
    ("new-secret", "new-secret", True),
    (None, "old-secret", True),
    ("", "anything", True),
])
async def test_modal_defers_before_query_and_uses_current_password(
    public, current_password, entered, allowed, monkeypatch
):
    view = (make_public_view if public else make_private_view)(
        [make_resource(1, version="v1", password="old-secret")]
    )
    select = view.children[0]
    select._values = ["1"]
    opening = make_bot_interaction()
    await select.callback(opening)
    modal = opening.response.send_modal.await_args.args[0]
    modal.password_input._value = entered
    submitted = make_bot_interaction(bot=opening.client)
    current = make_dto(password=current_password)
    current.source_message_id = 999
    context = make_session_context()

    async def enter_session():
        assert submitted.response.is_done()
        return MagicMock()

    context.__aenter__.side_effect = enter_session
    monkeypatch.setattr("src.ui.password_modal.AsyncSessionLocal", lambda: context)
    monkeypatch.setattr("src.ui.resource_select_view.AsyncSessionLocal", make_session_context)
    submitted.client.download_service.get_selected_resource = AsyncMock(return_value=current)
    await modal.on_submit(submitted)
    submitted.response.defer.assert_awaited_once_with(ephemeral=True, thinking=public)
    query = submitted.client.download_service.get_selected_resource.await_args.args[1]
    assert query.public_thread_id == 300 and query.resource_id == 1
    submitted.response.send_message.assert_not_awaited()
    if allowed:
        submitted.client.download_service.fetch_fresh_url.assert_awaited_once_with(current)
        submitted.client.dispatch.assert_called_once_with("resource_downloaded", current)
        assert modal.panel_view.selected_resource_id == 1
    else:
        submitted.client.download_service.fetch_fresh_url.assert_not_awaited()
        submitted.client.dispatch.assert_not_called()
        response = submitted.edit_original_response if public else submitted.followup.send
        kwargs = response.await_args.kwargs
        embed = kwargs["embeds"][0] if public else kwargs["embed"]
        assert embed.title == "❌ 密码错误"
        if not public:
            submitted.edit_original_response.assert_not_awaited()
    budget = await submitted.client.rate_limit_service.check(RateLimitQo(123, charge=False))
    assert budget.daily_count == int(allowed)


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
async def test_new_password_on_stale_unprotected_list_blocks_delivery(public, monkeypatch):
    view = (make_public_view if public else make_private_view)([make_resource(1, version="v1")])
    select = view.children[0]
    select._values = ["1"]
    interaction = make_bot_interaction()

    async def selected(*args):
        assert interaction.response.is_done()
        return make_dto(password="new-secret")

    interaction.client.download_service.get_selected_resource = AsyncMock(side_effect=selected)
    monkeypatch.setattr("src.ui.resource_select.AsyncSessionLocal", make_session_context)
    await select.callback(interaction)
    interaction.response.send_modal.assert_not_awaited()
    interaction.client.download_service.fetch_fresh_url.assert_not_awaited()
    interaction.client.dispatch.assert_not_called()
    response = interaction.edit_original_response if public else interaction.followup.send
    assert "密码设置已变化" in response.await_args.kwargs["content"]
    assert (await interaction.client.rate_limit_service.check(RateLimitQo(123, charge=False))).daily_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
async def test_password_trace_confirmation_after_defer_does_not_charge(public, monkeypatch):
    view = (make_public_view if public else make_private_view)(
        [make_resource(1, version="v1", password="secret")]
    )
    select = view.children[0]
    select._values = ["1"]
    opening = make_bot_interaction()
    await select.callback(opening)
    modal = opening.response.send_modal.await_args.args[0]
    modal.password_input._value = "secret"
    submitted = make_bot_interaction(bot=opening.client)
    submitted.client.download_service.get_selected_resource = AsyncMock(
        return_value=make_dto(password="secret", trace_enabled=True)
    )
    monkeypatch.setattr("src.ui.password_modal.AsyncSessionLocal", make_session_context)
    await modal.on_submit(submitted)
    submitted.response.defer.assert_awaited_once_with(ephemeral=True, thinking=public)
    submitted.response.send_message.assert_not_awaited()
    response = submitted.edit_original_response if public else submitted.followup.send
    consent = response.await_args.kwargs["view"]
    assert consent.resource.trace_enabled
    assert consent.panel_view is not modal.panel_view
    submitted.client.download_service.fetch_delivery.assert_not_called()
    assert (await submitted.client.rate_limit_service.check(RateLimitQo(123, charge=False))).daily_count == 0
    if public:
        submitted.followup.send.assert_not_awaited()
    else:
        submitted.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("state", ["finished", "busy", "deleted", "query_error"])
async def test_modal_failure_preserves_panel_and_never_delivers(public, state, monkeypatch):
    view = (make_public_view if public else make_private_view)(
        [make_resource(1, version="v1", password="secret")]
    )
    view.children[0]._values = ["1"]
    opening = make_bot_interaction()
    await view.children[0].callback(opening)
    modal = opening.response.send_modal.await_args.args[0]
    submitted = make_bot_interaction(bot=opening.client)
    query = AsyncMock(return_value=None)
    submitted.client.download_service.get_selected_resource = query
    monkeypatch.setattr("src.ui.password_modal.AsyncSessionLocal", make_session_context)
    if state == "finished":
        modal.panel_view.stop()
    elif state == "busy":
        await modal.panel_view.state_lock.acquire()
    elif state == "query_error":
        query.side_effect = RuntimeError("database unavailable")
    try:
        await modal.on_submit(submitted)
    finally:
        if state == "busy":
            modal.panel_view.state_lock.release()
    assert submitted.response.is_done()
    submitted.client.download_service.fetch_fresh_url.assert_not_awaited()
    response = submitted.edit_original_response if public else submitted.followup.send
    response.assert_awaited_once()
    if state in {"finished", "busy"}:
        query.assert_not_awaited()
    assert (await submitted.client.rate_limit_service.check(RateLimitQo(123, charge=False))).daily_count == 0


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
