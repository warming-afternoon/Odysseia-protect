import pytest

from src.dto.resource_dto import ResourceDTO
from src.services.download_service import DownloadService
from src.ui.download_entry_ui import DownloadEntryView
from src.ui.password_input_modal import PasswordModal


@pytest.mark.asyncio
async def test_download_entry_view_is_persistent():
    view = DownloadEntryView()

    assert view.timeout is None
    assert len(view.children) == 1
    assert view.children[0].custom_id == "odysseia-protect:open-download-panel"


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


@pytest.mark.asyncio
async def test_password_modal_preserves_panel_visibility_mode():
    resource = ResourceDTO(
        id=1,
        filename="card.png",
        version_info="v1",
        password="secret",
        source_message_id=2,
        public_thread_id=3,
    )

    assert PasswordModal(resource, edit_in_place=True).edit_in_place is True
    assert PasswordModal(resource).edit_in_place is False
