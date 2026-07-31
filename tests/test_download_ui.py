import pytest
import discord
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from src.database.models import UploadMode
from src.dto.resource_dto import ResourceDTO
from src.services.download_service import DownloadService
from src.ui.password_input_modal import PasswordModal
from src.ui.resource_select_view import ResourceSelectView


def test_download_embed_exposes_copyable_url_and_png_preview():
    resource = ResourceDTO(
        id=1,
        filename="card.png",
        version_info="v1",
        source_message_id=2,
        public_thread_id=3,
    )
    url = "https://cdn.discordapp.com/attachments/example/card.png"

    embed = DownloadService.build_download_embed(resource, url)

    assert url in (embed.description or "")
    assert embed.image.url == url


def test_download_embed_skips_preview_for_non_image():
    resource = ResourceDTO(
        id=1,
        filename="cards.zip",
        version_info="bundle",
        source_message_id=2,
        public_thread_id=3,
    )

    embed = DownloadService.build_download_embed(resource, "https://example.com/cards.zip")

    assert embed.image.url is None


def make_resource(
    resource_id: int,
    *,
    version: str,
    password: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=resource_id,
        filename=f"card-{resource_id}.png",
        version_info=version,
        password=password,
        source_message_id=resource_id + 100,
        upload_mode=UploadMode.SECURE,
        thread=SimpleNamespace(
            warehouse_thread_id=200,
            public_thread_id=300,
        ),
    )


@pytest.mark.asyncio
async def test_download_select_includes_normal_and_secure_resources():
    secure = make_resource(1, version="secure")
    normal = make_resource(2, version="normal")
    normal.upload_mode = UploadMode.NORMAL

    view = ResourceSelectView([secure, normal])
    select = view.children[0]

    assert len(select.options) == 2
    assert select.options[0].label.startswith("🔒")
    assert select.options[1].label.startswith("📄")
    assert view.children[1].label == "加入心愿单"
    assert view.children[1].disabled is True
    assert view.children[2].label == "从心愿单中移除"
    assert view.children[2].disabled is True


def make_interaction(download_service: MagicMock) -> SimpleNamespace:
    client = MagicMock()
    client.download_service = download_service
    client.wishlist_service = MagicMock()
    client.wishlist_service.is_wishlisted = AsyncMock(return_value=False)
    client.dispatch = MagicMock()
    return SimpleNamespace(
        response=SimpleNamespace(
            defer=AsyncMock(),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        client=client,
        user=SimpleNamespace(id=123),
        message=None,
    )


def make_session_context() -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=MagicMock())
    context.__aexit__ = AsyncMock(return_value=None)
    return context


@pytest.mark.asyncio
async def test_resource_select_replaces_result_embed_in_private_panel():
    first_resource = make_resource(1, version="v1")
    second_resource = make_resource(2, version="v2")
    resource_list_embed = discord.Embed(title="📄 版本选择")
    first_download_embed = discord.Embed(title="📥 v1")
    second_download_embed = discord.Embed(title="📥 v2")

    view = ResourceSelectView(
        [first_resource, second_resource],
        resource_list_embed=resource_list_embed,
    )
    select = view.children[0]

    download_service = MagicMock()
    download_service.fetch_fresh_url = AsyncMock(
        side_effect=["https://example.com/v1.png", "https://example.com/v2.png"]
    )
    download_service.build_download_embed = MagicMock(
        side_effect=[first_download_embed, second_download_embed]
    )
    interaction = make_interaction(download_service)

    repository = MagicMock()
    repository.get_with_thread = AsyncMock(
        side_effect=[first_resource, second_resource]
    )
    session_context = make_session_context()

    with (
        patch(
            "src.ui.resource_select.AsyncSessionLocal",
            return_value=session_context,
        ),
        patch(
            "src.ui.resource_select.ResourceRepository",
            return_value=repository,
        ),
    ):
        select._values = ["1"]
        await select.callback(interaction)
        select._values = ["2"]
        await select.callback(interaction)

    assert interaction.response.defer.await_args_list == [call(), call()]
    assert interaction.edit_original_response.await_args_list == [
        call(
            embeds=[first_download_embed, resource_list_embed],
            view=view,
        ),
        call(
            embeds=[second_download_embed, resource_list_embed],
            view=view,
        ),
    ]
    interaction.followup.send.assert_not_awaited()
    assert interaction.client.dispatch.call_count == 2


@pytest.mark.asyncio
async def test_resource_select_failure_keeps_panel_and_sends_private_error():
    resource = make_resource(1, version="v1")
    resource_list_embed = discord.Embed(title="📄 版本选择")
    view = ResourceSelectView(
        [resource],
        resource_list_embed=resource_list_embed,
    )
    select = view.children[0]
    select._values = ["1"]

    download_service = MagicMock()
    download_service.fetch_fresh_url = AsyncMock(
        side_effect=RuntimeError("attachment unavailable")
    )
    interaction = make_interaction(download_service)

    repository = MagicMock()
    repository.get_with_thread = AsyncMock(return_value=resource)

    with (
        patch(
            "src.ui.resource_select.AsyncSessionLocal",
            return_value=make_session_context(),
        ),
        patch(
            "src.ui.resource_select.ResourceRepository",
            return_value=repository,
        ),
    ):
        await select.callback(interaction)

    interaction.response.defer.assert_awaited_once_with()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_expired_resource_select_points_to_available_download_entries():
    resource = make_resource(1, version="v1")
    view = ResourceSelectView([resource])
    select = view.children[0]
    select._values = ["1"]
    interaction = make_interaction(MagicMock())

    repository = MagicMock()
    repository.get_with_thread = AsyncMock(return_value=resource)

    with (
        patch(
            "src.ui.resource_select.AsyncSessionLocal",
            return_value=make_session_context(),
        ),
        patch(
            "src.ui.resource_select.ResourceRepository",
            return_value=repository,
        ),
    ):
        await select.callback(interaction)

    interaction.response.send_message.assert_awaited_once_with(
        "❌ 下载面板状态已失效，请重新使用 `/下载` 或右键“打开下载面板”。",
        ephemeral=True,
    )


@pytest.mark.asyncio
async def test_password_modal_updates_original_private_panel():
    resource = ResourceDTO(
        id=1,
        filename="card.png",
        version_info="v1",
        password="secret",
        source_message_id=2,
        public_thread_id=3,
    )
    resource_list_embed = discord.Embed(title="📄 版本选择")
    result_embed = discord.Embed(title="📥 角色卡下载")
    panel_view = discord.ui.View()
    modal = PasswordModal(
        resource,
        resource_list_embed=resource_list_embed,
        panel_view=panel_view,
    )
    modal.password_input._value = "secret"

    download_service = MagicMock()
    download_service.fetch_fresh_url = AsyncMock(
        return_value="https://example.com/card.png"
    )
    download_service.build_download_embed = MagicMock(return_value=result_embed)
    interaction = make_interaction(download_service)

    await modal.on_submit(interaction)

    interaction.response.defer.assert_awaited_once_with()
    interaction.edit_original_response.assert_awaited_once_with(
        embeds=[result_embed, resource_list_embed],
        view=panel_view,
    )
    interaction.followup.send.assert_not_awaited()
    interaction.client.dispatch.assert_called_once_with(
        "resource_downloaded",
        resource,
    )


@pytest.mark.asyncio
async def test_password_modal_rejects_wrong_password_privately():
    resource = ResourceDTO(
        id=1,
        filename="card.png",
        version_info="v1",
        password="secret",
        source_message_id=2,
        public_thread_id=3,
    )
    modal = PasswordModal(
        resource,
        resource_list_embed=discord.Embed(title="📄 版本选择"),
        panel_view=discord.ui.View(),
    )
    modal.password_input._value = "wrong"
    interaction = make_interaction(MagicMock())

    await modal.on_submit(interaction)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.response.defer.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
