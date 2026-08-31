"""LSB 像素水印实验实现。

该模块只用于比较实验，不接入 Bot 生产链。水印写入 RGBA 图片的 RGB
最低有效位，Alpha 通道保持不变；PNG 原有非像素 Chunk 会原样保留。
"""

from __future__ import annotations

import hashlib
import io
import struct
import zlib
from collections.abc import Iterator

from PIL import Image

from Watermark.watermark import PNGChunk, PNGFormatError, parse_png, serialize_png


LSB_MAGIC = b"ODYL"
LSB_VERSION = 1
LSB_HEADER = struct.Struct(">4sBII")


class LSBWatermarkError(ValueError):
    """LSB 水印无法注入或恢复。"""


def _packet(payload: bytes) -> bytes:
    return LSB_HEADER.pack(
        LSB_MAGIC,
        LSB_VERSION,
        len(payload),
        zlib.crc32(payload) & 0xFFFFFFFF,
    ) + payload


def _bits(data: bytes) -> Iterator[int]:
    for value in data:
        for shift in range(7, -1, -1):
            yield (value >> shift) & 1


def _bytes(bits: list[int]) -> bytes:
    if len(bits) % 8:
        raise LSBWatermarkError("LSB 位流长度不是完整字节。")
    return bytes(
        sum(bits[offset + bit] << (7 - bit) for bit in range(8))
        for offset in range(0, len(bits), 8)
    )


def _validate_repetition(repetition: int) -> None:
    if repetition < 1 or repetition % 2 == 0:
        raise LSBWatermarkError("repetition 必须是正奇数。")


def _positions(
    capacity: int,
    count: int,
    *,
    key: bytes | None,
    width: int,
    height: int,
) -> list[int]:
    if count > capacity:
        raise LSBWatermarkError(
            f"图片容量不足：需要 {count} bits，可用 {capacity} bits。"
        )
    if key is None:
        return list(range(count))

    # ponytail: 这是实验用的确定性散布算法，不承担密码学保密；生产升级时
    # 可以替换为正式定义的密钥派生与位置置换协议，但无需影响载荷格式。
    seed = hashlib.sha256(
        b"odysseia-lsb-placement\0"
        + key
        + struct.pack(">II", width, height)
    ).digest()
    result: list[int] = []
    used: set[int] = set()
    counter = 0
    while len(result) < count:
        digest = hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
        counter += 1
        for offset in range(0, len(digest), 4):
            candidate = int.from_bytes(digest[offset : offset + 4], "big") % capacity
            if candidate in used:
                continue
            used.add(candidate)
            result.append(candidate)
            if len(result) == count:
                break
    return result


def _byte_index(channel_index: int) -> int:
    pixel, channel = divmod(channel_index, 3)
    return pixel * 4 + channel


def _replace_pixels(source_png: bytes, pixel_png: bytes) -> bytes:
    source_chunks = parse_png(source_png)
    pixel_chunks = parse_png(pixel_png)
    new_ihdr = next(chunk for chunk in pixel_chunks if chunk.chunk_type == b"IHDR")
    new_idat = [chunk for chunk in pixel_chunks if chunk.chunk_type == b"IDAT"]

    source_ihdr = next(chunk for chunk in source_chunks if chunk.chunk_type == b"IHDR")
    if source_ihdr.data[:8] != new_ihdr.data[:8]:
        raise PNGFormatError("像素替换前后图片尺寸不一致。")

    output: list[PNGChunk] = []
    inserted_idat = False
    for chunk in source_chunks:
        if chunk.chunk_type == b"IHDR":
            output.append(new_ihdr)
        elif chunk.chunk_type == b"IDAT":
            if not inserted_idat:
                output.extend(new_idat)
                inserted_idat = True
        else:
            output.append(chunk)
    return serialize_png(output)


def _rgba(png_data: bytes) -> tuple[int, int, bytearray]:
    with Image.open(io.BytesIO(png_data)) as image:
        rgba = image.convert("RGBA")
        return rgba.width, rgba.height, bytearray(rgba.tobytes())


def embed_lsb(
    png_data: bytes,
    payload: bytes,
    *,
    key: bytes | None = None,
    repetition: int = 1,
) -> bytes:
    """把完整自包含载荷写入 RGB LSB，并保留原 PNG 非像素 Chunk。"""

    _validate_repetition(repetition)
    width, height, pixels = _rgba(png_data)
    packet = _packet(payload)
    logical_bits = list(_bits(packet))
    encoded_bits = [bit for bit in logical_bits for _ in range(repetition)]
    positions = _positions(
        width * height * 3,
        len(encoded_bits),
        key=key,
        width=width,
        height=height,
    )

    for position, bit in zip(positions, encoded_bits, strict=True):
        index = _byte_index(position)
        pixels[index] = (pixels[index] & 0xFE) | bit

    image = Image.frombytes("RGBA", (width, height), bytes(pixels))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=6)
    return _replace_pixels(png_data, buffer.getvalue())


def _read_logical_bits(
    pixels: bytearray,
    positions: list[int],
    *,
    repetition: int,
) -> list[int]:
    raw = [pixels[_byte_index(position)] & 1 for position in positions]
    threshold = repetition // 2 + 1
    return [
        int(sum(raw[offset : offset + repetition]) >= threshold)
        for offset in range(0, len(raw), repetition)
    ]


def extract_lsb(
    png_data: bytes,
    *,
    key: bytes | None = None,
    repetition: int = 1,
) -> bytes:
    """恢复并以 Magic、版本、长度与 CRC32 校验 LSB 载荷。"""

    _validate_repetition(repetition)
    width, height, pixels = _rgba(png_data)
    capacity = width * height * 3
    header_bits = LSB_HEADER.size * 8
    header_positions = _positions(
        capacity,
        header_bits * repetition,
        key=key,
        width=width,
        height=height,
    )
    header = _bytes(
        _read_logical_bits(pixels, header_positions, repetition=repetition)
    )
    magic, version, payload_length, expected_crc = LSB_HEADER.unpack(header)
    if magic != LSB_MAGIC or version != LSB_VERSION:
        raise LSBWatermarkError("未找到有效的 Odysseia LSB 水印头。")

    packet_length = LSB_HEADER.size + payload_length
    if packet_length * 8 * repetition > capacity:
        raise LSBWatermarkError("LSB 水印声明长度超出图片容量。")
    positions = _positions(
        capacity,
        packet_length * 8 * repetition,
        key=key,
        width=width,
        height=height,
    )
    packet = _bytes(_read_logical_bits(pixels, positions, repetition=repetition))
    payload = packet[LSB_HEADER.size :]
    if zlib.crc32(payload) & 0xFFFFFFFF != expected_crc:
        raise LSBWatermarkError("LSB 水印 CRC32 校验失败。")
    return payload


def logical_bit_error_rate(
    png_data: bytes,
    payload: bytes,
    *,
    key: bytes | None = None,
    repetition: int = 1,
) -> float:
    """返回经历破坏后的多数表决逻辑位错误率。"""

    _validate_repetition(repetition)
    width, height, pixels = _rgba(png_data)
    expected = list(_bits(_packet(payload)))
    positions = _positions(
        width * height * 3,
        len(expected) * repetition,
        key=key,
        width=width,
        height=height,
    )
    actual = _read_logical_bits(pixels, positions, repetition=repetition)
    errors = sum(left != right for left, right in zip(expected, actual, strict=True))
    return errors / len(expected)
