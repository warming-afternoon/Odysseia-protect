from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.cogs.event_handler_cog import EventHandlerCog
from src.database.repositories.thread import ThreadRepository
from src.database.schemas import ThreadCreate
from src.enums import SourceStatus
from src.scripts import backfill_thread_sources


@pytest.mark.asyncio
async def test_thread_repository_tracks_missing_and_updated_source(db_session):
    repository = ThreadRepository()
    thread = await repository.create(
        db_session,
        obj_in=ThreadCreate(public_thread_id=123, author_id=456),
    )
    await db_session.flush()

    assert await repository.get_missing_source_metadata(db_session) == [thread]

    updated = await repository.update_source_metadata(
        db_session,
        public_thread_id=123,
        guild_id=789,
        public_thread_name="新的帖子标题",
        source_status=SourceStatus.ACTIVE,
    )

    assert updated is thread
    assert thread.guild_id == 789
    assert thread.public_thread_name == "新的帖子标题"
    assert thread.source_status == SourceStatus.ACTIVE
    assert await repository.get_missing_source_metadata(db_session) == []


@pytest.mark.asyncio
async def test_raw_thread_update_syncs_latest_title():
    cog = EventHandlerCog(MagicMock())
    cog._update_source_metadata = AsyncMock()
    payload = SimpleNamespace(
        thread_id=123,
        guild_id=789,
        data={"name": "改名后的帖子"},
    )

    await cog.on_raw_thread_update(payload)

    cog._update_source_metadata.assert_awaited_once_with(
        public_thread_id=123,
        guild_id=789,
        public_thread_name="改名后的帖子",
        source_status=SourceStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_raw_thread_delete_only_marks_source_deleted():
    cog = EventHandlerCog(MagicMock())
    cog._update_source_metadata = AsyncMock()
    payload = SimpleNamespace(thread_id=123, guild_id=789)

    await cog.on_raw_thread_delete(payload)

    cog._update_source_metadata.assert_awaited_once_with(
        public_thread_id=123,
        guild_id=789,
        source_status=SourceStatus.DELETED,
    )


@pytest.mark.asyncio
async def test_backfill_updates_only_missing_source_metadata(monkeypatch):
    thread = SimpleNamespace(public_thread_id=123)
    repository = MagicMock()
    repository.get_missing_source_metadata = AsyncMock(return_value=[thread])
    repository.update_source_metadata = AsyncMock()

    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)

    channel = SimpleNamespace(
        name="历史帖子",
        guild=SimpleNamespace(id=789),
    )
    client = MagicMock()
    client.login = AsyncMock()
    client.fetch_channel = AsyncMock(return_value=channel)
    client.close = AsyncMock()

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(backfill_thread_sources, "init_db", AsyncMock())
    monkeypatch.setattr(
        backfill_thread_sources,
        "ThreadRepository",
        lambda: repository,
    )
    monkeypatch.setattr(
        backfill_thread_sources,
        "AsyncSessionLocal",
        lambda: session_context,
    )
    monkeypatch.setattr(
        backfill_thread_sources.discord,
        "Client",
        lambda **_: client,
    )
    monkeypatch.setattr(
        backfill_thread_sources,
        "engine",
        SimpleNamespace(dispose=AsyncMock()),
    )

    result = await backfill_thread_sources.backfill()

    assert result == 0
    client.fetch_channel.assert_awaited_once_with(123)
    repository.update_source_metadata.assert_awaited_once_with(
        session,
        public_thread_id=123,
        guild_id=789,
        public_thread_name="历史帖子",
        source_status=SourceStatus.ACTIVE,
    )
    session.commit.assert_awaited_once()
