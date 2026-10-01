import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.database.repositories.resource import ResourceRepository
from src.database.repositories.thread import ThreadRepository
from src.database.repositories.user import UserRepository
from src.dto.download_page_dto import DownloadPageDTO
from src.dto.download_resource_option_dto import DownloadResourceOptionDTO
from src.dto.resource_dto import ResourceDTO
from src.enums import UploadMode
from src.models import Resource, Thread
from src.qo.download_page_qo import DownloadPageQo
from src.qo.resource_selection_qo import ResourceSelectionQo
from src.services.delivery_service import DeliveryResult
from src.services.download_service import DownloadService
from src.ui.download_page_jump_modal import DownloadPageJumpModal
from src.ui.password_modal import PasswordModal
from src.ui.public_resource_select_view import PublicResourceSelectView
from src.ui.resource_select_view import ResourceSelectView
from src.ui.trace_consent_view import send_trace_consent
from tests.test_download_ui import make_interaction, make_session_context


def page_data(total, page=1):
    max_page = max(1, (total + 24) // 25)
    page = min(page, max_page)
    end = total - (page - 1) * 25
    return DownloadPageDTO(
        public_thread_id=300, guild_id=400, total=total, page=page, max_page=max_page,
        items=tuple(DownloadResourceOptionDTO(
            id=i, version_info=f"v{i}", filename=f"card-{i}.png",
            source_message_id=i + 100, upload_mode=UploadMode.SECURE,
            created_at=datetime(2026, 10, 1),
        ) for i in range(end, max(0, end - 25), -1)),
    )


def service():
    return DownloadService(MagicMock(), ResourceRepository(), ThreadRepository(), UserRepository())


async def seed(session, total):
    thread = Thread(public_thread_id=300, guild_id=400, author_id=123)
    session.add(thread)
    await session.flush()
    session.add_all([Resource(
        thread_id=thread.id, version_info=f"v{i}", filename=f"card-{i}.png",
        source_message_id=i + 100, upload_mode=UploadMode.SECURE,
        created_at=datetime(2026, 10, 1),
    ) for i in range(1, total + 1)])
    await session.flush()
    return thread


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [0, 1, 25, 26, 50, 51])
async def test_database_pages_cover_every_version_with_fixed_query_count(db_session, total):
    await seed(db_session, total)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db_session.bind.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        seen = []
        for page in range(1, max(1, (total + 24) // 25) + 1):
            statements.clear()
            result = await service().get_page(db_session, DownloadPageQo(public_thread_id=300, page=page))
            assert len(statements) == 3
            assert len(result.items) <= 25
            assert result.total == total
            assert "LIMIT" in statements[-1] and "OFFSET" in statements[-1]
            seen.extend(item.version_info for item in result.items)
        assert seen == [f"v{i}" for i in range(total, 0, -1)]
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.mark.asyncio
async def test_service_orders_by_upload_time_then_id(db_session):
    await seed(db_session, 3)
    resources = await ResourceRepository().get_by_thread_id(db_session, thread_id=1)
    resources[0].created_at += timedelta(days=1)
    await db_session.flush()
    result = await service().get_page(db_session, DownloadPageQo(public_thread_id=300))
    assert [item.id for item in result.items] == [1, 3, 2]


@pytest.mark.asyncio
async def test_deleted_versions_clamp_page_and_new_uploads_appear(db_session):
    thread = await seed(db_session, 26)
    result = await service().get_page(db_session, DownloadPageQo(public_thread_id=300, page=2))
    assert [item.id for item in result.items] == [1]
    await db_session.execute(delete(Resource).where(Resource.id == 1))
    result = await service().get_page(db_session, DownloadPageQo(public_thread_id=300, page=2))
    assert result.page == result.max_page == 1
    assert result.total == 25
    db_session.add(Resource(
        thread_id=thread.id, version_info="new", filename="new.png", source_message_id=999,
        upload_mode=UploadMode.NORMAL, created_at=datetime(2026, 10, 2),
    ))
    await db_session.flush()
    result = await service().get_page(db_session, DownloadPageQo(public_thread_id=300))
    assert result.items[0].version_info == "new"
    assert result.max_page == 2
    await db_session.execute(delete(Resource))
    result = await service().get_page(db_session, DownloadPageQo(public_thread_id=300, page=2))
    assert result.total == 0 and result.page == 1 and not result.items


@pytest.mark.asyncio
async def test_dtos_render_after_session_closes_and_selection_is_scoped(async_engine):
    factory = async_sessionmaker(async_engine, expire_on_commit=True)
    async with factory() as session:
        await seed(session, 26)
        data = await service().get_page(session, DownloadPageQo(public_thread_id=300, page=2))
        resource = await service().get_selected_resource(session, ResourceSelectionQo(
            public_thread_id=300, resource_id=1,
        ))
        other = Thread(public_thread_id=301, author_id=123)
        session.add(other)
        await session.flush()
        assert await service().get_selected_resource(session, ResourceSelectionQo(
            public_thread_id=301, resource_id=1,
        )) is None
        assert await service().get_selected_resource(session, ResourceSelectionQo(
            public_thread_id=300, resource_id=999,
        )) is None
        await session.commit()
    view = ResourceSelectView(data)
    assert view.children[0].options[0].value == "1"
    assert resource.public_thread_id == 300
    assert "card-1.png" in view.resource_list_embed.fields[0].value


@pytest.mark.asyncio
@pytest.mark.parametrize("view_type", [ResourceSelectView, PublicResourceSelectView])
@pytest.mark.parametrize("total", [0, 1, 25, 26, 50, 51])
async def test_navigation_only_appears_when_more_than_25_versions(view_type, total):
    view = view_type(page_data(total))
    buttons = [item for item in view.children if item.row == 1]
    assert len(buttons) == (5 if total > 25 else 0)
    if buttons:
        assert [button.label for button in buttons] == ["⏮️", "◀️", f"1/{view.page_data.max_page}", "▶️", "⏭️"]
        assert buttons[0].disabled and buttons[1].disabled
        assert buttons[2].style is discord.ButtonStyle.primary
    assert view.children[0].disabled is (total == 0)


@pytest.mark.asyncio
async def test_private_buttons_navigate_and_clear_download_state():
    view = ResourceSelectView(page_data(51), user_id=123)
    view.selected_resource_id = 51
    view.set_wishlist_state(True)
    mock_service = MagicMock()
    mock_service.get_page = AsyncMock(side_effect=lambda session, qo: page_data(51, qo.page))
    interaction = make_interaction(mock_service)
    with patch("src.ui.download_pagination_view.AsyncSessionLocal", return_value=make_session_context()):
        for label, expected_page in [("▶️", 2), ("⏭️", 3), ("◀️", 2), ("⏮️", 1)]:
            button = next(item for item in view.children if getattr(item, "label", None) == label)
            await button.callback(interaction)
            assert view.page_data.page == expected_page
            assert interaction.edit_original_response.await_args.kwargs["view"] is view
            assert interaction.edit_original_response.await_args.kwargs["attachments"] == []
    assert view.selected_resource_id is None
    assert view.add_button.disabled and view.remove_button.disabled


@pytest.mark.asyncio
async def test_public_pagination_creates_independent_personal_views():
    view = PublicResourceSelectView(page_data(51))
    mock_service = MagicMock()
    mock_service.get_page = AsyncMock(side_effect=lambda session, qo: page_data(51, qo.page))
    interactions = [make_interaction(mock_service), make_interaction(mock_service)]
    interactions[1].user.id = 456
    with patch("src.ui.download_pagination_view.AsyncSessionLocal", return_value=make_session_context()):
        for interaction in interactions:
            await view.go_to_page(interaction, 2)
            interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    private_views = [interaction.edit_original_response.await_args.kwargs["view"] for interaction in interactions]
    assert private_views[0] is not private_views[1]
    assert [panel.user_id for panel in private_views] == [123, 456]
    assert all(panel.page_data.page == 2 for panel in private_views)
    assert view.page_data.page == 1
    assert not await private_views[0].interaction_check(interactions[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["abc", "0", "-1", "4", "1.5"])
async def test_invalid_jump_does_not_change_panel(value):
    view = ResourceSelectView(page_data(51))
    modal = DownloadPageJumpModal(view)
    modal.page_input._value = value
    interaction = make_interaction(MagicMock())
    await modal.on_submit(interaction)
    interaction.response.send_message.assert_awaited_once()
    interaction.edit_original_response.assert_not_awaited()
    assert view.page_data.page == 1


@pytest.mark.asyncio
async def test_jump_modal_and_last_page_boundaries():
    view = ResourceSelectView(page_data(51))
    mock_service = MagicMock()
    mock_service.get_page = AsyncMock(return_value=page_data(51, 3))
    interaction = make_interaction(mock_service)
    await view.show_page_jump_modal(interaction)
    modal = interaction.response.send_modal.await_args.args[0]
    modal.page_input._value = "3"
    with patch("src.ui.download_pagination_view.AsyncSessionLocal", return_value=make_session_context()):
        await modal.on_submit(interaction)
    assert view.page_data.page == 3
    assert all(button.disabled for button in view.children if getattr(button, "label", None) in {"▶️", "⏭️"})
    assert [option.value for option in view.children[0].options] == ["1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [25, 0])
async def test_refresh_removes_navigation_and_handles_empty_page(total):
    view = ResourceSelectView(page_data(26, 2))
    mock_service = MagicMock()
    mock_service.get_page = AsyncMock(return_value=page_data(total))
    interaction = make_interaction(mock_service)
    with patch("src.ui.download_pagination_view.AsyncSessionLocal", return_value=make_session_context()):
        await view.go_to_page(interaction, 2)
    assert view.page_data.page == 1
    assert not [item for item in view.children if item.row == 1]
    assert view.children[0].disabled is (total == 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["query", "edit"])
async def test_failed_navigation_keeps_previous_page_and_selection(failure):
    view = ResourceSelectView(page_data(51))
    view.selected_resource_id = 51
    view.set_wishlist_state(True)
    previous_ids = [item.custom_id for item in view.children]
    mock_service = MagicMock()
    mock_service.get_page = AsyncMock(return_value=page_data(51, 2))
    interaction = make_interaction(mock_service)
    if failure == "query":
        mock_service.get_page.side_effect = RuntimeError("database unavailable")
    else:
        interaction.edit_original_response.side_effect = RuntimeError("edit unavailable")
    with patch("src.ui.download_pagination_view.AsyncSessionLocal", return_value=make_session_context()):
        await view.go_to_page(interaction, 2)
    assert view.page_data.page == 1 and view.selected_resource_id == 51
    assert view.add_button.disabled and not view.remove_button.disabled
    assert [item.custom_id for item in view.children] == previous_ids
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_rapid_next_clicks_query_and_edit_in_order():
    view = ResourceSelectView(page_data(76))
    requested = []

    async def fetch(session, qo):
        requested.append(qo.page)
        await asyncio.sleep(0.01)
        return page_data(76, qo.page)

    mock_service = MagicMock()
    mock_service.get_page = AsyncMock(side_effect=fetch)
    interactions = [make_interaction(mock_service) for _ in range(3)]
    button = next(item for item in view.children if getattr(item, "label", None) == "▶️")
    with patch("src.ui.download_pagination_view.AsyncSessionLocal", return_value=make_session_context()):
        await asyncio.gather(*(button.callback(interaction) for interaction in interactions))
    assert requested == [2, 3, 4]
    assert view.page_data.page == 4


@pytest.mark.asyncio
async def test_last_page_selection_downloads_oldest_version():
    view = ResourceSelectView(page_data(26, 2))
    selected = ResourceDTO(
        id=1, filename="oldest.png", version_info="v1", source_message_id=101,
        public_thread_id=300, upload_mode=UploadMode.SECURE,
    )
    mock_service = MagicMock()
    mock_service.get_selected_resource = AsyncMock(return_value=selected)
    mock_service.fetch_fresh_url = AsyncMock(return_value="https://example.com/oldest.png")
    interaction = make_interaction(mock_service)
    select = view.children[0]
    select._values = ["1"]
    with (
        patch("src.ui.resource_select.AsyncSessionLocal", return_value=make_session_context()),
        patch("src.ui.resource_select_view.AsyncSessionLocal", return_value=make_session_context()),
    ):
        await select.callback(interaction)
    assert mock_service.get_selected_resource.await_args.args[1].resource_id == 1
    mock_service.fetch_fresh_url.assert_awaited_once_with(selected)
    assert view.selected_resource_id == 1 and view.page_data.page == 2


@pytest.mark.asyncio
async def test_trace_confirmation_retains_page_in_independent_view():
    view = ResourceSelectView(page_data(26, 2))
    resource = ResourceDTO(
        id=1, filename="trace.png", version_info="v1", source_message_id=101,
        public_thread_id=300, trace_enabled=True,
    )
    mock_service = MagicMock()
    mock_service.fetch_delivery = AsyncMock(return_value=DeliveryResult(
        filename="trace.png", url="https://example.com/trace.png",
    ))
    interaction = make_interaction(mock_service)
    await send_trace_consent(
        interaction, resource=resource, resource_list_embed=view.resource_list_embed, panel_view=view,
    )
    consent = interaction.response.send_message.await_args.kwargs["view"]
    assert consent.panel_view is not view
    confirm = make_interaction(mock_service)
    with patch("src.ui.resource_select_view.AsyncSessionLocal", return_value=make_session_context()):
        await consent.agree.callback(confirm)
    panel = confirm.edit_original_response.await_args.kwargs["view"]
    assert panel.page_data.page == 2 and panel.selected_resource_id == 1
    assert view.selected_resource_id is None


@pytest.mark.asyncio
async def test_password_opened_before_paging_cannot_restore_previous_page():
    view = ResourceSelectView(page_data(26))
    resource = ResourceDTO(
        id=26, filename="password.png", version_info="v26", source_message_id=126,
        public_thread_id=300, password="secret",
    )
    modal = PasswordModal(resource, resource_list_embed=view.resource_list_embed, panel_view=view)
    modal.password_input._value = "secret"
    view.page_data = page_data(26, 2)
    view.render_page()
    interaction = make_interaction(MagicMock())
    await modal.on_submit(interaction)
    interaction.response.send_message.assert_awaited_once_with(
        "❌ 页面已变化，请重新选择资源。", ephemeral=True,
    )
    interaction.edit_original_response.assert_not_awaited()
    assert view.page_data.page == 2


@pytest.mark.asyncio
async def test_wishlist_buttons_use_selected_version_on_historical_page():
    view = ResourceSelectView(page_data(26, 2))
    view.selected_resource_id = 1
    view.set_wishlist_state(False)
    interaction = make_interaction(MagicMock())
    interaction.client.wishlist_service.add = AsyncMock(return_value="added")
    interaction.client.wishlist_service.remove = AsyncMock(return_value="removed")
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    context = make_session_context()
    context.__aenter__.return_value = session
    with (
        patch("src.ui.add_to_wishlist_button.AsyncSessionLocal", return_value=context),
        patch("src.ui.remove_from_wishlist_button.AsyncSessionLocal", return_value=context),
    ):
        await view.add_button.callback(interaction)
        assert view.add_button.disabled and not view.remove_button.disabled
        await view.remove_button.callback(interaction)
        assert not view.add_button.disabled and view.remove_button.disabled
    assert interaction.client.wishlist_service.add.await_args.kwargs["resource_id"] == 1
    assert interaction.client.wishlist_service.remove.await_args.kwargs["resource_id"] == 1
    assert view.page_data.page == 2


@pytest.mark.asyncio
async def test_download_count_updates_in_repo_and_ignores_deleted_resource(db_session):
    await seed(db_session, 1)
    repository = ResourceRepository()
    assert await repository.increment_download_count(db_session, resource_id=1)
    assert await repository.increment_download_count(db_session, resource_id=1)
    resource = await repository.get(db_session, id=1)
    assert resource.download_count == 2
    assert not await repository.increment_download_count(db_session, resource_id=999)
