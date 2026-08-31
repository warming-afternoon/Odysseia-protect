from pathlib import Path

import pytest

from src.services.traceability_service import (
    TraceabilityService,
    TraceabilityUnavailableError,
)
from src.traceability.watermark import WatermarkError, verify_watermark


CARD_PATH = Path(__file__).parents[1] / "Watermark" / "default_Seraphina.png"
KEY = bytes(range(32))


@pytest.mark.asyncio
async def test_personalize_binds_user_thread_and_resource():
    service = TraceabilityService(key=KEY, key_id="test-v1")

    result = await service.personalize(
        CARD_PATH.read_bytes(),
        filename="Seraphina.png",
        user_id=123456,
        public_thread_id=789,
        resource_id=42,
    )

    verified = verify_watermark(
        result.data,
        keys={"test-v1": KEY},
        expected_card_id="discord-thread:789",
        strict_layers=True,
    )
    assert result.filename == "Seraphina.personalized.png"
    assert verified["payload"]["uid"] == "123456"
    assert verified["payload"]["resource_id"] == "42"


def test_validate_character_card_rejects_non_png():
    with pytest.raises(WatermarkError, match="仅支持 PNG"):
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
