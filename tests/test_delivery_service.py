from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.dto.resource_dto import ResourceDTO
from src.services.delivery_service import DeliveryService
from src.services.traceability_service import PersonalizedCard


class FakeStore:
    def __init__(self, *, available=True):
        self.available = available
        self.put_bytes = AsyncMock()
        self.presign_get = AsyncMock(side_effect=lambda key, **_: f"https://r2/{key}")
        self.delete_many = AsyncMock()


def resource() -> ResourceDTO:
    return ResourceDTO(
        id=9,
        filename="card.png",
        source_message_id=99,
        warehouse_thread_id=88,
        public_thread_id=77,
        trace_enabled=True,
    )


@pytest.mark.asyncio
async def test_same_user_resource_reuses_local_cache():
    traceability = SimpleNamespace(
        key_id="v1",
        personalize=AsyncMock(
            return_value=PersonalizedCard(b"personalized", "card.personalized.png")
        ),
    )
    store = FakeStore()
    service = DeliveryService(
        traceability,
        store,
        redis_url="",
        download_ttl=1800,
        delete_grace=300,
    )
    source_loader = AsyncMock(return_value=b"source")

    first = await service.deliver(resource(), user_id=123, source_loader=source_loader)
    second = await service.deliver(resource(), user_id=123, source_loader=source_loader)

    assert first.url and second.url == first.url
    assert first.cache_hit is False
    assert second.cache_hit is True
    source_loader.assert_awaited_once()
    traceability.personalize.assert_awaited_once()
    store.put_bytes.assert_awaited_once()
    assert store.presign_get.await_count == 2


@pytest.mark.asyncio
async def test_r2_unavailable_falls_back_to_private_attachment_bytes():
    traceability = SimpleNamespace(
        key_id="v1",
        personalize=AsyncMock(
            return_value=PersonalizedCard(b"personalized", "card.personalized.png")
        ),
    )
    service = DeliveryService(traceability, FakeStore(available=False), redis_url="")

    result = await service.deliver(
        resource(), user_id=123, source_loader=AsyncMock(return_value=b"source")
    )

    assert result.url is None
    assert result.file_data == b"personalized"


@pytest.mark.asyncio
async def test_local_cleanup_batches_at_one_thousand():
    traceability = SimpleNamespace(key_id="v1")
    store = FakeStore()
    service = DeliveryService(traceability, store, redis_url="")
    entry_type = __import__(
        "src.services.delivery_service", fromlist=["_LocalEntry"]
    )._LocalEntry
    for index in range(1001):
        service._local_entries[str(index)] = entry_type(
            object_key=f"deliveries/{index}",
            filename="card.png",
            cache_expires_at=0,
            delete_after=0,
        )

    assert await service.cleanup_once() == 1000
    assert await service.cleanup_once() == 1
    assert store.delete_many.await_count == 2
    first_batch = list(store.delete_many.await_args_list[0].args[0])
    assert len(first_batch) == 1000
