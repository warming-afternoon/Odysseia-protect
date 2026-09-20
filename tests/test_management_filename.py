import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.database.models import Resource, Thread, UploadMode
from src.database.repositories.resource import ResourceRepository
from src.services.management_service import ManagementService


@pytest.mark.asyncio
@pytest.mark.parametrize("trace_enabled", [False, True])
@pytest.mark.parametrize(
    ("title", "expected"),
    [("类脑娘-圣诞拥抱.png", "类脑娘-圣诞拥抱.png"), (None, "-.png"), ("", "-.png")],
)
async def test_replace_preserves_attachment_title(db_session, title, expected, trace_enabled):
    thread = Thread(public_thread_id=10, warehouse_thread_id=20, author_id=30)
    db_session.add(thread)
    await db_session.flush()
    resource = Resource(
        thread_id=thread.id, version_info="v1", upload_mode=UploadMode.SECURE,
        source_message_id=40, filename="old.png", trace_enabled=trace_enabled,
    )
    db_session.add(resource)
    await db_session.commit()
    resource_id = resource.id

    bot = MagicMock()
    bot.traceability_service = MagicMock(available=True)
    warehouse = MagicMock(spec=discord.Thread)
    warehouse.send = AsyncMock(return_value=SimpleNamespace(id=50))
    warehouse.fetch_message = AsyncMock(return_value=SimpleNamespace(delete=AsyncMock()))
    bot.fetch_channel = AsyncMock(return_value=warehouse)
    service = ManagementService(bot, ResourceRepository(), MagicMock(), MagicMock())

    async def to_file(*, filename):
        return discord.File(io.BytesIO(b"test"), filename=filename)

    attachment = SimpleNamespace(
        title=title, filename="-.png", size=4,
        read=AsyncMock(return_value=b"test"), to_file=AsyncMock(side_effect=to_file),
    )
    await service.replace_resource_source(
        db_session, resource_id=resource_id,
        interaction=SimpleNamespace(user=SimpleNamespace(id=30), channel_id=10, guild_id=None),
        attachment=attachment, expected_source_message_id=40,
    )

    await db_session.refresh(resource)
    assert resource.filename == expected
    assert resource.source_message_id == 50
    assert warehouse.send.await_args.kwargs["file"].filename == expected
    if trace_enabled:
        bot.traceability_service.validate_character_card.assert_called_once_with(expected, b"test")
    else:
        attachment.to_file.assert_awaited_once_with(filename=expected)
