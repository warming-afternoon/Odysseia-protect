from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.cogs.StellariaForwarderCog import StellariaForwarderCog


def configure_forwarder(
    monkeypatch: pytest.MonkeyPatch, *, enabled: str = "true"
) -> None:
    monkeypatch.setenv("STELLARIA_FORWARDER_ENABLED", enabled)
    monkeypatch.setenv("STELLARIA_DISCUSSION_FORUM_ID", "123")
    monkeypatch.setenv("STELLARIA_EVENT_API_BASE_URL", "http://stellaria-pact-api:8765")
    monkeypatch.setenv("STELLARIA_EVENT_API_TOKEN", "shared-secret")


def make_message(
    *,
    content: str = "valid message",
    forum_id: int = 123,
    message_id: int = 1001,
    user_id: int = 2001,
) -> MagicMock:
    channel = MagicMock(spec=discord.Thread)
    channel.id = 3001
    channel.parent_id = forum_id

    message = MagicMock(spec=discord.Message)
    message.id = message_id
    message.content = content
    message.channel = channel
    message.guild = MagicMock(id=4001)
    message.author = MagicMock(id=user_id, bot=False)
    return message


@pytest.mark.asyncio
async def test_invalid_configuration_refuses_to_enable(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("STELLARIA_FORWARDER_ENABLED", "true")
    monkeypatch.delenv("STELLARIA_DISCUSSION_FORUM_ID", raising=False)
    monkeypatch.delenv("STELLARIA_EVENT_API_BASE_URL", raising=False)
    monkeypatch.delenv("STELLARIA_EVENT_API_TOKEN", raising=False)

    cog = StellariaForwarderCog(MagicMock())
    await cog.cog_load()

    assert cog.enabled is False
    assert cog._session is None


@pytest.mark.parametrize(
    "content", ["", "abcd", "\U0001f600 \U0001f600", "<:wave:123456>"]
)
def test_message_validation_rejects_non_qualifying_content(
    monkeypatch: pytest.MonkeyPatch, content: str
):
    configure_forwarder(monkeypatch)
    cog = StellariaForwarderCog(MagicMock())
    assert cog.is_valid_message(make_message(content=content)) is False


@pytest.mark.asyncio
async def test_create_and_delete_forward_only_target_forum(
    monkeypatch: pytest.MonkeyPatch,
):
    configure_forwarder(monkeypatch)
    cog = StellariaForwarderCog(MagicMock())
    cog._forward_event = AsyncMock()

    target_message = make_message()
    other_message = make_message(forum_id=999, message_id=1002)

    await cog.on_message(target_message)
    await cog.on_message(other_message)
    await cog.on_message_delete(target_message)

    assert cog._forward_event.await_args_list[0].args == (
        "message_created",
        target_message,
    )
    assert cog._forward_event.await_args_list[1].args == (
        "message_deleted",
        target_message,
    )
    assert cog._forward_event.await_count == 2


@pytest.mark.asyncio
async def test_disabled_forwarder_ignores_messages(monkeypatch: pytest.MonkeyPatch):
    configure_forwarder(monkeypatch, enabled="false")
    cog = StellariaForwarderCog(MagicMock())
    cog._forward_event = AsyncMock()

    await cog.on_message(make_message())

    cog._forward_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_delete_forwards_only_cached_qualifying_messages(
    monkeypatch: pytest.MonkeyPatch,
):
    configure_forwarder(monkeypatch)
    cog = StellariaForwarderCog(MagicMock())
    cog._forward_event = AsyncMock()

    valid = make_message(message_id=1001)
    too_short = make_message(content="no", message_id=1002)
    other_forum = make_message(forum_id=999, message_id=1003)

    await cog.on_bulk_message_delete([valid, too_short, other_forum])

    cog._forward_event.assert_awaited_once_with("message_deleted", valid)


@pytest.mark.asyncio
async def test_http_request_contains_only_contract_fields_and_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
):
    configure_forwarder(monkeypatch)
    cog = StellariaForwarderCog(MagicMock())
    response = MagicMock(status=200)
    response.text = AsyncMock(return_value="")
    request_context = MagicMock()
    request_context.__aenter__ = AsyncMock(return_value=response)
    request_context.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock(closed=False)
    session.post.return_value = request_context
    cog._session = session  # type: ignore[assignment]

    await cog._forward_event("message_created", make_message())

    args, kwargs = session.post.call_args
    assert args == ("http://stellaria-pact-api:8765/api/v1/message-events",)
    assert kwargs["headers"] == {"Authorization": "Bearer shared-secret"}
    assert kwargs["json"] == {
        "schema_version": 1,
        "event_type": "message_created",
        "message_id": "1001",
        "guild_id": "4001",
        "forum_id": "123",
        "thread_id": "3001",
        "user_id": "2001",
    }


@pytest.mark.asyncio
async def test_http_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch):
    configure_forwarder(monkeypatch)
    cog = StellariaForwarderCog(MagicMock())
    response = MagicMock(status=503)
    response.text = AsyncMock(return_value="service unavailable")
    request_context = MagicMock()
    request_context.__aenter__ = AsyncMock(return_value=response)
    request_context.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock(closed=False)
    session.post.return_value = request_context
    cog._session = session  # type: ignore[assignment]

    await cog._forward_event("message_created", make_message())

    session.post.assert_called_once()
