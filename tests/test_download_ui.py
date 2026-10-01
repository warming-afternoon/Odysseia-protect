from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import discord
import pytest

from src.enums import UploadMode
from src.enums import SourceStatus
from src.dto.resource_dto import ResourceDTO
from src.ui.download_embed_builder import DownloadEmbedBuilder
from src.dto.download_page_dto import DownloadPageDTO
from src.dto.download_resource_option_dto import DownloadResourceOptionDTO
from src.enums.download_response_mode import DownloadResponseMode
from src.ui.password_modal import PasswordModal
from src.ui.public_resource_select_view import PublicResourceSelectView
from src.ui.resource_select_view import ResourceSelectView


def make_page(resources, *, total=None, page=1):
    ordered = resources
    count = len(ordered) if total is None else total
    return DownloadPageDTO(
        public_thread_id=300, guild_id=400, total=count, page=page,
        max_page=max(1, (count + 24) // 25),
        items=tuple(DownloadResourceOptionDTO.model_validate(r) for r in ordered),
    )


def make_private_view(resources, *, resource_list_embed=None):
    view = ResourceSelectView(make_page(resources))
    if resource_list_embed is not None:
        view.resource_list_embed = resource_list_embed
        view.children[0].resource_list_embed = resource_list_embed
    return view


def make_public_view(resources, *, resource_list_embed=None):
    view = PublicResourceSelectView(make_page(resources))
    if resource_list_embed is not None:
        view.resource_list_embed = resource_list_embed
        view.children[0].resource_list_embed = resource_list_embed
    return view


def test_download_embed_exposes_copyable_url_and_png_preview():
    resource = ResourceDTO(
        id=1,
        filename="card.png",
        version_info="v1",
        source_message_id=2,
        public_thread_id=3,
    )
    url = "https://cdn.discordapp.com/attachments/example/card.png"

    embed = DownloadEmbedBuilder.build_download_embed(resource, url)

    assert url in (embed.description or "")
    assert f"```\n{url}\n```" in (embed.description or "")
    assert embed.image.url == url


def test_download_embed_skips_preview_for_non_image():
    resource = ResourceDTO(
        id=1,
        filename="cards.zip",
        version_info="bundle",
        source_message_id=2,
        public_thread_id=3,
    )

    embed = DownloadEmbedBuilder.build_download_embed(resource, "https://example.com/cards.zip")

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
        trace_enabled=False,
        source_message_id=resource_id + 100,
        upload_mode=UploadMode.SECURE,
        created_at=datetime(2026, 9, 5, 12, 34, 56),
        thread=SimpleNamespace(
            warehouse_thread_id=200,
            public_thread_id=300,
            author_id=500,
            guild_id=400,
            public_thread_name="来源帖子",
            source_status=SourceStatus.ACTIVE,
        ),
    )


@pytest.mark.asyncio
async def test_download_select_includes_dates_for_all_resource_modes():
    secure = make_resource(1, version="secure")
    normal = make_resource(2, version="normal")
    trace = make_resource(3, version="trace")
    normal.upload_mode = UploadMode.NORMAL
    trace.trace_enabled = True

    view = make_private_view([trace, normal, secure])
    select = view.children[0]

    assert len(select.options) == 3
    assert select.options[0].label.startswith("🔎")
    assert select.options[1].label.startswith("📄")
    assert select.options[2].label.startswith("🔒")
    assert [option.description for option in select.options] == [
        "2026/09/05 · 文件名: card-3.png",
        "2026/09/05 · 文件名: card-2.png",
        "2026/09/05 · 文件名: card-1.png",
    ]
    assert view.children[1].label == "加入心愿单"
    assert view.children[1].disabled is True
    assert view.children[2].label == "从心愿单中移除"
    assert view.children[2].disabled is True


def test_download_select_truncates_long_filename_after_date():
    resource = make_resource(1, version="v1")
    resource.filename = "a" * 150 + ".png"

    view = make_private_view([resource])
    description = view.children[0].options[0].description

    assert description is not None
    assert description.startswith("2026/09/05 · 文件名: ")
    assert description.endswith("...")
    assert len(description) == 100


def test_download_select_uses_na_for_missing_filename():
    resource = make_resource(1, version="v1")
    resource.filename = None

    view = make_private_view([resource])

    assert view.children[0].options[0].description == (
        "2026/09/05 · 文件名: N/A"
    )


@pytest.mark.asyncio
async def test_public_download_gateway_only_contains_a_short_lived_select():
    resource = make_resource(1, version="v1")
    view = make_public_view(
        [resource],
        resource_list_embed=discord.Embed(title="📄 版本选择"),
    )

    assert view.timeout == 60.0
    assert len(view.children) == 1
    assert view.children[0].response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL


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

    view = make_private_view(
        [first_resource, second_resource],
        resource_list_embed=resource_list_embed,
    )
    select = view.children[0]

    download_service = MagicMock()
    download_service.fetch_fresh_url = AsyncMock(
        side_effect=["https://example.com/v1.png", "https://example.com/v2.png"]
    )
    embed_builder = MagicMock(
        side_effect=[first_download_embed, second_download_embed]
    )
    interaction = make_interaction(download_service)

    interaction.client.download_service.get_selected_resource = AsyncMock(
        side_effect=[first_resource, second_resource]
    )
    session_context = make_session_context()

    with (
        patch(
            "src.ui.resource_select.AsyncSessionLocal",
            return_value=session_context,
        ),
    ):
        select._values = ["1"]
        with patch.object(DownloadEmbedBuilder, "build_download_embed", embed_builder):
            await select.callback(interaction)
        select._values = ["2"]
        with patch.object(DownloadEmbedBuilder, "build_download_embed", embed_builder):
            await select.callback(interaction)

    assert interaction.response.defer.await_args_list == [call(), call()]
    assert interaction.edit_original_response.await_args_list == [
        call(
            embeds=[first_download_embed, resource_list_embed],
            view=view,
            attachments=[],
        ),
        call(
            embeds=[second_download_embed, resource_list_embed],
            view=view,
            attachments=[],
        ),
    ]
    interaction.followup.send.assert_not_awaited()
    assert interaction.client.dispatch.call_count == 2


@pytest.mark.asyncio
async def test_public_download_gateway_creates_private_complete_panel():
    resource = make_resource(1, version="v1")
    resource_list_embed = discord.Embed(title="📄 版本选择")
    result_embed = discord.Embed(title="📥 角色卡下载")
    public_view = make_public_view(
        [resource],
        resource_list_embed=resource_list_embed,
    )
    select = public_view.children[0]
    select._values = ["1"]

    download_service = MagicMock()
    download_service.fetch_fresh_url = AsyncMock(
        return_value="https://example.com/card.png"
    )
    embed_builder = MagicMock(return_value=result_embed)
    interaction = make_interaction(download_service)
    public_message = SimpleNamespace(edit=AsyncMock())
    interaction.message = public_message

    interaction.client.download_service.get_selected_resource = AsyncMock(return_value=resource)

    with (
        patch(
            "src.ui.resource_select.AsyncSessionLocal",
            return_value=make_session_context(),
        ),
    ):
        with patch.object(DownloadEmbedBuilder, "build_download_embed", embed_builder):
            await select.callback(interaction)

    interaction.response.defer.assert_awaited_once_with(
        ephemeral=True,
        thinking=True,
    )
    interaction.followup.send.assert_not_awaited()
    public_message.edit.assert_not_awaited()

    private_panel = interaction.edit_original_response.await_args.kwargs["view"]
    assert isinstance(private_panel, ResourceSelectView)
    assert len(private_panel.children) == 3
    assert private_panel.selected_resource_id == resource.id
    interaction.edit_original_response.assert_awaited_once_with(
        embeds=[result_embed, resource_list_embed],
        view=private_panel,
        attachments=[],
    )


@pytest.mark.asyncio
async def test_public_password_selection_creates_private_complete_panel():
    resource = make_resource(1, version="v1", password="secret")
    resource_list_embed = discord.Embed(title="📄 版本选择")
    result_embed = discord.Embed(title="📥 角色卡下载")
    public_view = make_public_view(
        [resource],
        resource_list_embed=resource_list_embed,
    )
    select = public_view.children[0]
    select._values = ["1"]

    download_service = MagicMock()
    interaction = make_interaction(download_service)
    public_message = SimpleNamespace(edit=AsyncMock())
    interaction.message = public_message
    interaction.client.download_service.get_selected_resource = AsyncMock(return_value=resource)

    with (
        patch(
            "src.ui.resource_select.AsyncSessionLocal",
            return_value=make_session_context(),
        ),
    ):
        await select.callback(interaction)

    interaction.response.send_modal.assert_awaited_once()
    public_message.edit.assert_not_awaited()
    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, PasswordModal)
    assert modal.response_mode is DownloadResponseMode.CREATE_PRIVATE_PANEL
    assert isinstance(modal.panel_view, ResourceSelectView)

    modal.password_input._value = "secret"
    download_service.fetch_fresh_url = AsyncMock(
        return_value="https://example.com/card.png"
    )
    embed_builder = MagicMock(return_value=result_embed)
    modal_interaction = make_interaction(download_service)

    with patch.object(DownloadEmbedBuilder, "build_download_embed", embed_builder):
        await modal.on_submit(modal_interaction)

    modal_interaction.response.defer.assert_awaited_once_with(
        ephemeral=True,
        thinking=True,
    )
    modal_interaction.edit_original_response.assert_awaited_once_with(
        embeds=[result_embed, resource_list_embed],
        view=modal.panel_view,
        attachments=[],
    )
    assert modal.panel_view.selected_resource_id == resource.id


@pytest.mark.asyncio
async def test_private_password_selection_only_opens_modal_without_editing_panel():
    resource = make_resource(1, version="v1", password="secret")
    view = make_private_view(
        [resource],
        resource_list_embed=discord.Embed(title="📄 版本选择"),
    )
    select = view.children[0]
    select._values = ["1"]
    interaction = make_interaction(MagicMock())
    private_message = SimpleNamespace(edit=AsyncMock())
    interaction.message = private_message
    interaction.client.download_service.get_selected_resource = AsyncMock(return_value=resource)

    with (
        patch(
            "src.ui.resource_select.AsyncSessionLocal",
            return_value=make_session_context(),
        ),
    ):
        await select.callback(interaction)

    interaction.response.send_modal.assert_awaited_once()
    private_message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_download_gateway_reports_link_failure_privately():
    resource = make_resource(1, version="v1")
    public_view = make_public_view(
        [resource],
        resource_list_embed=discord.Embed(title="📄 版本选择"),
    )
    select = public_view.children[0]
    select._values = ["1"]

    download_service = MagicMock()
    download_service.fetch_fresh_url = AsyncMock(
        side_effect=RuntimeError("attachment unavailable")
    )
    interaction = make_interaction(download_service)
    public_message = SimpleNamespace(edit=AsyncMock())
    interaction.message = public_message
    interaction.client.download_service.get_selected_resource = AsyncMock(return_value=resource)

    with (
        patch(
            "src.ui.resource_select.AsyncSessionLocal",
            return_value=make_session_context(),
        ),
    ):
        await select.callback(interaction)

    interaction.response.defer.assert_awaited_once_with(
        ephemeral=True,
        thinking=True,
    )
    interaction.edit_original_response.assert_awaited_once_with(
        content=(
            "❌ 抱歉，获取下载链接时发生错误。"
            "源文件可能已被删除或Bot无法访问。"
        ),
        embeds=[],
        view=None,
    )
    interaction.followup.send.assert_not_awaited()
    public_message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_select_failure_keeps_panel_and_sends_private_error():
    resource = make_resource(1, version="v1")
    resource_list_embed = discord.Embed(title="📄 版本选择")
    view = make_private_view(
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

    interaction.client.download_service.get_selected_resource = AsyncMock(return_value=resource)

    with (
        patch(
            "src.ui.resource_select.AsyncSessionLocal",
            return_value=make_session_context(),
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
    view = make_private_view([resource])
    select = view.children[0]
    select._values = ["1"]
    interaction = make_interaction(MagicMock())
    view.stop()

    interaction.client.download_service.get_selected_resource = AsyncMock(return_value=resource)

    with (
        patch(
            "src.ui.resource_select.AsyncSessionLocal",
            return_value=make_session_context(),
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
    embed_builder = MagicMock(return_value=result_embed)
    interaction = make_interaction(download_service)

    with patch.object(DownloadEmbedBuilder, "build_download_embed", embed_builder):
        await modal.on_submit(interaction)

    interaction.response.defer.assert_awaited_once_with()
    interaction.edit_original_response.assert_awaited_once_with(
        embeds=[result_embed, resource_list_embed],
        view=panel_view,
        attachments=[],
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
