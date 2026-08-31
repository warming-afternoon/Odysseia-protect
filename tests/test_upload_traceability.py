from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.config import TRACE_MAX_SOURCE_BYTES
from src.services.traceability_service import TraceabilityService
from src.services.upload_service import UploadService

CARD_PATH = Path(__file__).parents[1] / "Watermark" / "default_Seraphina.png"


@pytest.mark.asyncio
async def test_secure_upload_validates_and_persists_trace_opt_in():
    bot = MagicMock()
    bot.traceability_service = TraceabilityService(
        key=bytes(range(32)), key_id="test-v1"
    )
    resource_repo = MagicMock()
    resource_repo.create = AsyncMock()
    service = UploadService(bot, resource_repo, MagicMock(), MagicMock())
    service._get_or_create_thread = AsyncMock(return_value=SimpleNamespace(id=5))
    warehouse = MagicMock(spec=discord.Thread)
    warehouse.id = 456
    warehouse.send = AsyncMock(return_value=SimpleNamespace(id=999))
    service._find_or_create_warehouse_thread = AsyncMock(return_value=warehouse)
    attachment = MagicMock()
    attachment.filename = "card.png"
    card_data = CARD_PATH.read_bytes()
    attachment.size = len(card_data)
    attachment.read = AsyncMock(return_value=card_data)
    attachment.to_file = AsyncMock()
    interaction = MagicMock()
    interaction.channel = MagicMock(spec=discord.Thread)

    result = await service._handle_secure_upload(
        MagicMock(),
        interaction=interaction,
        file=attachment,
        version_info="v1",
        password=None,
        trace_enabled=True,
    )

    assert "已开启动态溯源" in result
    attachment.to_file.assert_not_awaited()
    created = resource_repo.create.await_args.kwargs["obj_in"]
    assert created.trace_enabled is True


def _upload_fixture():
    bot = MagicMock()
    bot.traceability_service = MagicMock(available=True)
    resource_repo = MagicMock()
    resource_repo.create = AsyncMock()
    service = UploadService(bot, resource_repo, MagicMock(), MagicMock())
    service._get_or_create_thread = AsyncMock(return_value=SimpleNamespace(id=5))
    warehouse = MagicMock(spec=discord.Thread)
    warehouse.id = 456
    warehouse.send = AsyncMock(return_value=SimpleNamespace(id=999))
    service._find_or_create_warehouse_thread = AsyncMock(return_value=warehouse)
    interaction = MagicMock()
    interaction.channel = MagicMock(spec=discord.Thread)
    interaction.user.id = 123
    return service, resource_repo, warehouse, interaction


@pytest.mark.asyncio
async def test_trace_upload_rejects_oversize_metadata_before_reading():
    service, resource_repo, warehouse, interaction = _upload_fixture()
    attachment = MagicMock(spec=discord.Attachment)
    attachment.filename = "large.png"
    attachment.size = TRACE_MAX_SOURCE_BYTES + 1
    attachment.read = AsyncMock()
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    result = await service.handle_upload_submission(
        session,
        interaction=interaction,
        mode="secure",
        file=attachment,
        version_info="v1",
        password=None,
        trace_enabled=True,
    )

    assert "不超过 25 MB" in result
    assert "large.png" in result
    attachment.read.assert_not_awaited()
    service._get_or_create_thread.assert_not_awaited()
    service._find_or_create_warehouse_thread.assert_not_awaited()
    warehouse.send.assert_not_awaited()
    resource_repo.create.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_trace_upload_allows_exact_size_metadata():
    attachment = MagicMock(spec=discord.Attachment)
    attachment.filename = "exact.png"
    attachment.size = TRACE_MAX_SOURCE_BYTES

    UploadService._validate_trace_attachment_sizes([attachment])


@pytest.mark.asyncio
async def test_trace_upload_rechecks_actual_bytes_after_reading():
    service, resource_repo, warehouse, interaction = _upload_fixture()
    service._get_or_create_thread = AsyncMock(
        return_value=SimpleNamespace(id=5, author_id=interaction.user.id)
    )
    attachment = MagicMock(spec=discord.Attachment)
    attachment.filename = "mismatch.png"
    attachment.size = 1
    attachment.read = AsyncMock(return_value=b"x" * (TRACE_MAX_SOURCE_BYTES + 1))
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    result = await service.handle_upload_submission(
        session,
        interaction=interaction,
        mode="secure",
        file=attachment,
        version_info="v1",
        password=None,
        trace_enabled=True,
    )

    assert "不超过 25 MB" in result
    attachment.read.assert_awaited_once()
    warehouse.send.assert_not_awaited()
    resource_repo.create.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_trace_batch_rejects_all_before_reading_when_one_file_is_oversize():
    service, resource_repo, warehouse, interaction = _upload_fixture()
    service._get_or_create_thread = AsyncMock(
        return_value=SimpleNamespace(id=5, author_id=interaction.user.id)
    )
    small = MagicMock(spec=discord.Attachment)
    small.filename = "small.png"
    small.size = 1024
    small.read = AsyncMock()
    large = MagicMock(spec=discord.Attachment)
    large.filename = "large.png"
    large.size = TRACE_MAX_SOURCE_BYTES + 1
    large.read = AsyncMock()
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    result = await service.handle_secure_upload_submission_from_message(
        session,
        interaction=interaction,
        attachments=[small, large],
        version_info="v1",
        password=None,
        trace_enabled=True,
    )

    assert "large.png" in result
    small.read.assert_not_awaited()
    large.read.assert_not_awaited()
    warehouse.send.assert_not_awaited()
    resource_repo.create.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversize_secure_upload_still_works_when_trace_is_disabled():
    service, resource_repo, warehouse, interaction = _upload_fixture()
    attachment = MagicMock(spec=discord.Attachment)
    attachment.filename = "large.bin"
    attachment.size = TRACE_MAX_SOURCE_BYTES + 1
    attachment.to_file = AsyncMock(return_value=MagicMock(spec=discord.File))

    result = await service._handle_secure_upload(
        MagicMock(),
        interaction=interaction,
        file=attachment,
        version_info="v1",
        password=None,
        trace_enabled=False,
    )

    assert "上传成功" in result
    attachment.to_file.assert_awaited_once()
    warehouse.send.assert_awaited_once()
    resource_repo.create.assert_awaited_once()
