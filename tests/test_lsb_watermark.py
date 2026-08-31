import io
import random
from pathlib import Path

import pytest
from PIL import Image

from Watermark.lsb_watermark import (
    LSBWatermarkError,
    embed_lsb,
    extract_lsb,
)
from Watermark.watermark import card_documents, parse_png


CARD_PATH = Path(__file__).parents[1] / "Watermark" / "default_Seraphina.png"
PAYLOAD = b'{"ciphertext":"experiment","kid":"test-v1","v":1}'
PLACEMENT_KEY = bytes(range(32))


@pytest.fixture(scope="module")
def original() -> bytes:
    return CARD_PATH.read_bytes()


@pytest.mark.parametrize(
    ("key", "repetition"),
    [(None, 1), (PLACEMENT_KEY, 3)],
)
def test_lsb_round_trip_preserves_card_chunks_and_alpha(
    original: bytes, key: bytes | None, repetition: int
):
    watermarked = embed_lsb(original, PAYLOAD, key=key, repetition=repetition)

    assert extract_lsb(watermarked, key=key, repetition=repetition) == PAYLOAD
    original_chunks = parse_png(original)
    watermarked_chunks = parse_png(watermarked)
    assert [
        (chunk.chunk_type, chunk.data)
        for chunk in original_chunks
        if chunk.chunk_type not in {b"IHDR", b"IDAT", b"IEND"}
    ] == [
        (chunk.chunk_type, chunk.data)
        for chunk in watermarked_chunks
        if chunk.chunk_type not in {b"IHDR", b"IDAT", b"IEND"}
    ]
    assert {
        keyword: document
        for _, keyword, document in card_documents(original_chunks)
    } == {
        keyword: document
        for _, keyword, document in card_documents(watermarked_chunks)
    }

    with Image.open(io.BytesIO(original)) as source_image, Image.open(
        io.BytesIO(watermarked)
    ) as marked_image:
        assert source_image.convert("RGBA").getchannel("A").tobytes() == marked_image.convert(
            "RGBA"
        ).getchannel("A").tobytes()


def test_wrong_placement_key_is_rejected(original: bytes):
    watermarked = embed_lsb(original, PAYLOAD, key=PLACEMENT_KEY, repetition=3)

    with pytest.raises(LSBWatermarkError, match="水印头"):
        extract_lsb(watermarked, key=b"x" * 32, repetition=3)


def test_repetition_three_survives_one_flip_per_group(original: bytes):
    from Watermark import lsb_watermark

    watermarked = embed_lsb(original, PAYLOAD, key=PLACEMENT_KEY, repetition=3)
    width, height, pixels = lsb_watermark._rgba(watermarked)
    bit_count = len(lsb_watermark._packet(PAYLOAD)) * 8 * 3
    positions = lsb_watermark._positions(
        width * height * 3,
        bit_count,
        key=PLACEMENT_KEY,
        width=width,
        height=height,
    )
    for position in positions[::3]:
        index = lsb_watermark._byte_index(position)
        pixels[index] ^= 1

    image = Image.frombytes("RGBA", (width, height), bytes(pixels))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    damaged = lsb_watermark._replace_pixels(watermarked, buffer.getvalue())

    assert extract_lsb(damaged, key=PLACEMENT_KEY, repetition=3) == PAYLOAD


def test_plain_lsb_rejects_sparse_bit_damage(original: bytes):
    from Watermark import lsb_watermark

    watermarked = embed_lsb(original, PAYLOAD)
    width, height, pixels = lsb_watermark._rgba(watermarked)
    positions = lsb_watermark._positions(
        width * height * 3,
        len(lsb_watermark._packet(PAYLOAD)) * 8,
        key=None,
        width=width,
        height=height,
    )
    for position in random.Random(7).sample(positions, 3):
        pixels[lsb_watermark._byte_index(position)] ^= 1

    image = Image.frombytes("RGBA", (width, height), bytes(pixels))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    damaged = lsb_watermark._replace_pixels(watermarked, buffer.getvalue())

    with pytest.raises(LSBWatermarkError):
        extract_lsb(damaged)
