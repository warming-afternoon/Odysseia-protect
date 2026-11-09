from pathlib import Path

import pytest

from src.services.traceability_service import (
    TraceabilityService,
    TraceabilityUnavailableError,
)
from src.traceability.watermark import WatermarkError

CARD_PATH = Path(__file__).parents[1] / "Watermark" / "default_Seraphina.png"
KEY = bytes(range(32))


@pytest.mark.asyncio
@pytest.mark.parametrize("lsb_setting", [None, "true", "false"])
async def test_personalize_binds_user_thread_and_resource(monkeypatch, lsb_setting):
    import base64

    monkeypatch.setenv("TRACEABILITY_ENABLED", "true")
    monkeypatch.setenv("ODYSSEIA_TRACE_KEY", base64.urlsafe_b64encode(KEY).decode())
    monkeypatch.setenv("ODYSSEIA_TRACE_KEY_ID", "test-v1")
    monkeypatch.setenv("ODYSSEIA_TRACE_VERIFY_KEYS", "{}")
    monkeypatch.delenv("TRACE_LSB_ENABLED", raising=False)
    if lsb_setting is not None:
        monkeypatch.setenv("TRACE_LSB_ENABLED", lsb_setting)
    service = TraceabilityService.from_environment()

    result = await service.personalize(
        CARD_PATH.read_bytes(),
        filename="Seraphina.png",
        user_id=123456,
        public_thread_id=789,
        resource_id=42,
        author_id=987654,
    )

    verified = service.verify(
        result.data,
        expected_card_id="discord-thread:789",
        strict_layers=True,
    )
    assert result.filename == "Seraphina.personalized.png"
    assert verified["payload"]["uid"] == "123456"
    assert verified["payload"]["resource_id"] == "42"
    assert verified["payload"]["author_id"] == "987654"
    assert "content_sha256" not in verified["payload"]
    assert ("pixel:lsb" in verified["sources"]) == (lsb_setting != "false")


def test_validate_character_card_rejects_arbitrary_json():
    with pytest.raises(WatermarkError, match="name"):
        TraceabilityService.validate_character_card("card.json", b"{}")


@pytest.mark.asyncio
async def test_personalize_fails_closed_without_key():
    service = TraceabilityService(key=None, key_id=None)

    with pytest.raises(TraceabilityUnavailableError, match="尚未配置"):
        await service.personalize(
            CARD_PATH.read_bytes(),
            filename="Seraphina.png",
            user_id=1,
            public_thread_id=2,
            resource_id=3,
        )


@pytest.mark.asyncio
async def test_json_personalization_preserves_format_and_verifies():
    import json

    service = TraceabilityService(key=KEY, key_id="test-v1")
    source = json.dumps(
        {"name": "test", "personality": "Calm", "scenario": "", "mes_example": ""}
    ).encode()
    result = await service.personalize(
        source, filename="card.json", user_id=123, public_thread_id=456, resource_id=789
    )
    verified = service.verify(
        result.data, expected_card_id="discord-thread:456", expected_resource_id="789"
    )
    assert result.filename == "card.personalized.json"
    assert verified["content_status"] == "not_checked"
    assert verified["context_status"] == "matched"
    assert verified["sources"] == ["macro:standalone"]
    assert "odysseia_trace" not in json.loads(result.data).get("extensions", {})


@pytest.mark.asyncio
async def test_previous_key_retained_for_verification_only():
    old = TraceabilityService(key=KEY, key_id="old")
    card = await old.personalize(
        CARD_PATH.read_bytes(),
        filename="card.png",
        user_id=1,
        public_thread_id=2,
        resource_id=3,
    )
    rotated = TraceabilityService(
        key=b"n" * 32, key_id="new", verification_keys={"old": KEY}
    )
    assert rotated.verify(card.data)["key_id"] == "old"
    fresh = await rotated.personalize(
        CARD_PATH.read_bytes(),
        filename="card.png",
        user_id=1,
        public_thread_id=2,
        resource_id=3,
    )
    assert rotated.verify(fresh.data)["key_id"] == "new"
    assert TraceabilityService(key=b"n" * 32, key_id="new").key_id == "new"
    with pytest.raises(WatermarkError, match="缺少密钥版本"):
        TraceabilityService(key=b"n" * 32, key_id="new").verify(card.data)


@pytest.mark.parametrize("historical", ["[]", '{"old":123}', '{"old":"not-a-key"}'])
def test_invalid_history_fails_closed(monkeypatch, historical):
    import base64

    monkeypatch.setenv("TRACEABILITY_ENABLED", "true")
    monkeypatch.setenv("ODYSSEIA_TRACE_KEY", base64.urlsafe_b64encode(KEY).decode())
    monkeypatch.setenv("ODYSSEIA_TRACE_KEY_ID", "new")
    monkeypatch.setenv("ODYSSEIA_TRACE_VERIFY_KEYS", historical)
    assert TraceabilityService.from_environment().available is False


def test_keyring_rejects_reused_id_and_oversize():
    with pytest.raises(WatermarkError, match="冲突"):
        TraceabilityService(
            key=KEY, key_id="current", verification_keys={"current": b"z" * 32}
        )
    with pytest.raises(WatermarkError, match="最多"):
        TraceabilityService(
            key=KEY, key_id="current", verification_keys={str(i): KEY for i in range(8)}
        )


@pytest.mark.asyncio
async def test_lsb_survives_all_metadata_removal_and_key_rotation():
    from src.traceability.watermark import parse_png, serialize_png

    service = TraceabilityService(key=KEY, key_id="old")
    result = await service.personalize(
        CARD_PATH.read_bytes(),
        filename="card.png",
        user_id=1,
        public_thread_id=2,
        resource_id=3,
    )
    assert service.verify(result.data, strict_layers=True)["sources"][-2:] == [
        "pixel:lsb",
        "png:trAc",
    ]
    stripped = serialize_png(
        c for c in parse_png(result.data) if c.chunk_type in (b"IHDR", b"IDAT", b"IEND")
    )
    rotated = TraceabilityService(
        key=b"x" * 32, key_id="new", verification_keys={"old": KEY}
    )
    verified = rotated.verify(stripped)
    assert verified["sources"] == ["pixel:lsb"]
    assert verified["payload"]["uid"] == "1"
    assert verified["content_status"] == "not_checked"
    assert verified["layer_status"] == "degraded"


def test_cache_version_separates_options():
    base = TraceabilityService(key=KEY, key_id="test")
    lsb = TraceabilityService(key=KEY, key_id="test", lsb_enabled=False)
    empty = TraceabilityService(key=KEY, key_id="test", allow_empty_fields=False)
    assert len({base.cache_version, lsb.cache_version, empty.cache_version}) == 3


@pytest.mark.asyncio
async def test_lsb_reissue_after_rotation_has_no_old_identity():
    first = TraceabilityService(key=KEY, key_id="old", lsb_enabled=True)
    old = await first.personalize(
        CARD_PATH.read_bytes(),
        filename="card.png",
        user_id=1,
        public_thread_id=2,
        resource_id=3,
    )
    rotated = TraceabilityService(
        key=b"x" * 32, key_id="new", verification_keys={"old": KEY}, lsb_enabled=True
    )
    new = await rotated.personalize(
        old.data, filename="card.png", user_id=9, public_thread_id=2, resource_id=3
    )
    verified = rotated.verify(new.data, strict_layers=True)
    assert verified["payload"]["uid"] == "9"
    assert verified["key_id"] == "new"
    assert verified["layer_status"] == "complete"


@pytest.mark.asyncio
async def test_disabling_lsb_rejects_reissuing_existing_pixel_identity():
    marked_service = TraceabilityService(key=KEY, key_id="old", lsb_enabled=True)
    old = await marked_service.personalize(
        CARD_PATH.read_bytes(),
        filename="card.png",
        user_id=1,
        public_thread_id=2,
        resource_id=3,
    )
    plain_service = TraceabilityService(key=KEY, key_id="old", lsb_enabled=False)
    with pytest.raises(WatermarkError, match="已有像素水印"):
        await plain_service.personalize(
            old.data, filename="card.png", user_id=9, public_thread_id=2, resource_id=3
        )


@pytest.mark.asyncio
async def test_empty_first_is_default_and_no_extensions_created():
    import json

    service = TraceabilityService(key=KEY, key_id="current")
    source = json.dumps(
        {"name": "test", "personality": "Calm", "scenario": "", "mes_example": ""}
    ).encode()
    marked = await service.personalize(
        source, filename="card.json", user_id=1, public_thread_id=2, resource_id=3
    )
    result = service.verify(marked.data)
    assert result["payload"]["macro_field"] in ("scenario", "mes_example")
    assert result["payload"]["allow_empty_fields"] is True
    assert "extensions" not in json.loads(marked.data)


@pytest.mark.asyncio
async def test_palette_png_without_pixel_carrier_passes_strict():
    import io

    from PIL import Image

    from src.traceability.watermark import (
        build_text_chunk,
        encode_card_json,
        parse_png,
        serialize_png,
    )

    image = Image.new("P", (128, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    document = {
        "name": "test",
        "personality": "Calm",
        "scenario": "",
        "mes_example": "",
    }
    chunks = parse_png(buffer.getvalue())
    chunks.insert(-1, build_text_chunk("chara", encode_card_json(document)))
    service = TraceabilityService(key=KEY, key_id="current", lsb_enabled=False)
    marked = await service.personalize(
        serialize_png(chunks),
        filename="card.png",
        user_id=1,
        public_thread_id=2,
        resource_id=3,
    )
    result = service.verify(marked.data, strict_layers=True)
    assert result["layer_status"] == "complete"
    assert any(layer["status"] == "unavailable" for layer in result["layer_results"])
