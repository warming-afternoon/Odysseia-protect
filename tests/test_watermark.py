import base64
import copy
from pathlib import Path

import pytest

from Watermark.watermark import (
    PNGChunk,
    TRACE_CHUNK_TYPE,
    VerificationError,
    canonical_json,
    card_documents,
    inject_watermark,
    parse_png,
    serialize_png,
    verify_watermark,
)


CARD_PATH = Path(__file__).parents[1] / "Watermark" / "default_Seraphina.png"
KEY = bytes(range(32))
KEYS = {"test-v1": KEY}


@pytest.fixture(scope="module")
def original() -> bytes:
    return CARD_PATH.read_bytes()


@pytest.fixture(scope="module")
def watermarked(original: bytes) -> bytes:
    result, _ = inject_watermark(
        original,
        key=KEY,
        key_id="test-v1",
        user_id="999999999999999999",
        card_id="st-default-seraphina",
        resource_id="resource-1",
        issued_at=1_787_788_800,
        delivery_id="test-delivery-id",
    )
    return result


def _idat(data: bytes) -> list[bytes]:
    return [chunk.data for chunk in parse_png(data) if chunk.chunk_type == b"IDAT"]


def _clean_documents(data: bytes) -> dict[str, dict]:
    result = {}
    for _, keyword, document in card_documents(parse_png(data)):
        document = copy.deepcopy(document)
        document["data"]["extensions"].pop("odysseia_trace", None)
        result[keyword] = document
    return result


def _rewrite_envelope_everywhere(data: bytes, mutate) -> bytes:
    from Watermark.watermark import build_text_chunk, encode_card_json

    chunks = parse_png(data)
    documents = {index: (keyword, document) for index, keyword, document in card_documents(chunks)}
    output = []
    for index, chunk in enumerate(chunks):
        if chunk.chunk_type == TRACE_CHUNK_TYPE:
            envelope = mutate(__import__("json").loads(chunk.data))
            output.append(PNGChunk(TRACE_CHUNK_TYPE, canonical_json(envelope)))
        elif index in documents:
            keyword, document = documents[index]
            envelope = document["data"]["extensions"]["odysseia_trace"]
            document["data"]["extensions"]["odysseia_trace"] = mutate(envelope)
            output.append(build_text_chunk(keyword, encode_card_json(document)))
        else:
            output.append(chunk)
    return serialize_png(output)


def test_real_card_round_trip_and_context_validation(original: bytes, watermarked: bytes):
    result = verify_watermark(
        watermarked,
        keys=KEYS,
        expected_card_id="st-default-seraphina",
        strict_layers=True,
    )

    assert result["valid"] is True
    assert result["layer_status"] == "complete"
    assert result["sources"] == ["json:ccv3", "json:chara", "png:trAc"]
    assert result["payload"]["uid"] == "999999999999999999"
    assert result["payload"]["delivery_id"] == "test-delivery-id"
    assert _idat(watermarked) == _idat(original)
    assert _clean_documents(watermarked) == _clean_documents(original)


def test_wrong_key_is_rejected(watermarked: bytes):
    with pytest.raises(VerificationError, match="认证失败"):
        verify_watermark(watermarked, keys={"test-v1": b"x" * 32})


def test_cross_card_transplant_is_rejected(watermarked: bytes):
    with pytest.raises(VerificationError, match="Card ID 不匹配"):
        verify_watermark(
            watermarked,
            keys=KEYS,
            expected_card_id="different-card",
        )


def test_consistent_ciphertext_tampering_is_rejected(watermarked: bytes):
    def mutate(envelope):
        result = copy.deepcopy(envelope)
        ciphertext = bytearray(
            base64.urlsafe_b64decode(result["ciphertext"] + "==")
        )
        ciphertext[0] ^= 1
        result["ciphertext"] = base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode()
        return result

    tampered = _rewrite_envelope_everywhere(watermarked, mutate)
    with pytest.raises(VerificationError, match="认证失败"):
        verify_watermark(tampered, keys=KEYS)


def test_conflicting_layers_are_rejected(watermarked: bytes):
    chunks = parse_png(watermarked)
    output = []
    for chunk in chunks:
        if chunk.chunk_type == TRACE_CHUNK_TYPE:
            envelope = __import__("json").loads(chunk.data)
            envelope["kid"] = "conflict"
            chunk = PNGChunk(TRACE_CHUNK_TYPE, canonical_json(envelope))
        output.append(chunk)

    with pytest.raises(VerificationError, match="冲突凭证"):
        verify_watermark(serialize_png(output), keys=KEYS)


def test_single_remaining_layer_can_be_recovered_but_not_strict(watermarked: bytes):
    without_png_layer = serialize_png(
        chunk for chunk in parse_png(watermarked) if chunk.chunk_type != TRACE_CHUNK_TYPE
    )

    result = verify_watermark(without_png_layer, keys=KEYS)
    assert result["valid"] is True
    assert result["layer_status"] == "degraded"
    assert result["missing_sources"] == ["png:trAc"]

    with pytest.raises(VerificationError, match="缺少水印层"):
        verify_watermark(without_png_layer, keys=KEYS, strict_layers=True)


def test_png_layer_survives_json_watermark_removal(watermarked: bytes):
    from Watermark.watermark import build_text_chunk, encode_card_json

    chunks = parse_png(watermarked)
    documents = {
        index: (keyword, document)
        for index, keyword, document in card_documents(chunks)
    }
    output = []
    for index, chunk in enumerate(chunks):
        if index in documents:
            keyword, document = documents[index]
            document["data"]["extensions"].pop("odysseia_trace")
            chunk = build_text_chunk(keyword, encode_card_json(document))
        output.append(chunk)

    without_json_layer = serialize_png(output)
    result = verify_watermark(without_json_layer, keys=KEYS)

    assert result["valid"] is True
    assert result["sources"] == ["png:trAc"]
    assert result["missing_sources"] == ["json:ccv3", "json:chara"]
    with pytest.raises(VerificationError, match="缺少水印层"):
        verify_watermark(without_json_layer, keys=KEYS, strict_layers=True)


def test_reinjection_replaces_old_watermark(original: bytes, watermarked: bytes):
    reinjected, _ = inject_watermark(
        watermarked,
        key=KEY,
        key_id="test-v1",
        user_id="888888888888888888",
        card_id="st-default-seraphina",
        resource_id="resource-1",
        issued_at=1_787_788_801,
        delivery_id="replacement",
    )
    result = verify_watermark(reinjected, keys=KEYS, strict_layers=True)

    assert result["payload"]["uid"] == "888888888888888888"
    assert result["payload"]["delivery_id"] == "replacement"
    assert sum(c.chunk_type == TRACE_CHUNK_TYPE for c in parse_png(reinjected)) == 1
    assert _idat(reinjected) == _idat(original)
