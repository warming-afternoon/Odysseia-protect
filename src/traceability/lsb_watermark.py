"""可选像素层：只适用于未缩放、未有损重压缩的静态 PNG。

V2 协议固定为大端 ODYL/2/重复次数/载荷长度/16 字节 HMAC 标签，随后
附加不透明的加密凭证。位置算法为稀疏 Fisher–Yates：第 i 步从 [i,N)
均匀选择 j 并交换。随机流是 HMAC-SHA256(placement_key, 尺寸||计数器)，
按大端 uint64 读取并用拒绝采样消除模偏差。密钥通过 HMAC 域分离派生。
头和正文使用同一位置流，字节按高位优先，每位连续重复指定次数。
不提供截图、裁剪、重绘、缩放或 JPEG 抵抗保证。调用方必须验证加密凭证。
"""

from __future__ import annotations

import hashlib
import hmac
import io
import struct
import time
import zlib
from collections.abc import Iterator, Sequence

from .watermark import PNGChunk, WatermarkError, parse_png, serialize_png

MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 8_000_000
MAX_PAYLOAD_BYTES = 8192
MAX_KEYS = 8
MAX_SECONDS = 10.0
_HEADER = struct.Struct(">4sBBI16s")
_OLD_HEADER = struct.Struct(">4sBII")
_DOMAIN = b"odysseia-protect-trace/lsb/v2/"


class LSBWatermarkError(WatermarkError):
    """像素层不存在、无效或超过安全预算。"""


class ExtractionNotFound(LSBWatermarkError):
    """候选密钥下未发现像素水印头（也可能密钥不匹配）。"""


def _check_time(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise LSBWatermarkError("LSB 处理超过时间预算。")


def _validate(repetition: int) -> None:
    if type(repetition) is not int or repetition not in (1, 3, 5):
        raise LSBWatermarkError("LSB repetition 必须为 1、3 或 5。")


def _derive(key: bytes, purpose: bytes) -> bytes:
    if not isinstance(key, bytes) or len(key) != 32:
        raise LSBWatermarkError("LSB 必须使用 32 字节 AES 密钥。")
    return hmac.digest(key, _DOMAIN + purpose, "sha256")


def _load(data: bytes) -> tuple[list[PNGChunk], int, int, str, bytearray]:
    # 先检查 IHDR 和压缩输入大小，再允许 Pillow 解码，避免解压炸弹。
    if len(data) > MAX_INPUT_BYTES:
        raise LSBWatermarkError("PNG 超过像素层输入上限。")
    chunks = parse_png(data)
    if len(chunks[0].data) != 13:
        raise LSBWatermarkError("IHDR 长度无效。")
    width, height, depth, color, comp, filt, interlace = struct.unpack(
        ">IIBBBBB", chunks[0].data
    )
    if not width or not height or width * height > MAX_PIXELS:
        raise LSBWatermarkError("PNG 像素数超过安全上限。")
    if depth != 8 or color not in (2, 6) or comp or filt or interlace:
        raise LSBWatermarkError("像素层仅支持非交错 8-bit RGB/RGBA PNG。")
    if any(c.chunk_type in (b"acTL", b"fcTL", b"fdAT", b"tRNS") for c in chunks):
        raise LSBWatermarkError("不支持动画或 tRNS 透明色 PNG。")
    if sum(c.chunk_type == b"IHDR" for c in chunks) != 1:
        raise LSBWatermarkError("PNG 存在重复 IHDR。")
    mode = "RGBA" if color == 6 else "RGB"
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            if image.mode != mode or image.size != (width, height):
                raise LSBWatermarkError("PNG 解码模式不一致。")
            pixels = bytearray(image.tobytes())
    except ImportError as exc:
        raise LSBWatermarkError("像素层需要安装 Pillow 可选依赖。") from exc
    except (OSError, ValueError) as exc:
        raise LSBWatermarkError("PNG 像素解码失败。") from exc
    return chunks, width, height, mode, pixels


def _positions(
    capacity: int, key: bytes, width: int, height: int, deadline: float
) -> Iterator[int]:
    """稳定 V2 置换，不分配与图片容量等大的列表。"""
    placement_key = _derive(key, b"placement")
    dimensions = struct.pack(">II", width, height)
    swaps: dict[int, int] = {}
    counter = 0
    words: list[int] = []
    for i in range(capacity):
        if i % 1024 == 0:
            _check_time(deadline)
        remaining = capacity - i
        ceiling = (1 << 64) - (1 << 64) % remaining
        for _ in range(128):
            if not words:
                block = hmac.digest(
                    placement_key, dimensions + struct.pack(">Q", counter), "sha256"
                )
                counter += 1
                words = list(reversed(struct.unpack(">QQQQ", block)))
            value = words.pop()
            if value < ceiling:
                break
        else:
            raise LSBWatermarkError("位置采样超过预算。")
        j = i + value % remaining
        selected = swaps.get(j, j)
        replacement = swaps.pop(i, i)
        if j != i:
            swaps[j] = replacement
        yield selected


def _legacy_positions(
    capacity: int, key: bytes | None, width: int, height: int, deadline: float
) -> Iterator[int]:
    """兼容实验 V1；去重采样设硬预算，绝不自动降级。"""
    if key is None:
        yield from range(capacity)
        return
    seed = hashlib.sha256(
        b"odysseia-lsb-placement\0" + key + struct.pack(">II", width, height)
    ).digest()
    used: set[int] = set()
    for counter in range(250_000):
        if counter % 128 == 0:
            _check_time(deadline)
        digest = hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
        for offset in range(0, 32, 4):
            candidate = int.from_bytes(digest[offset : offset + 4], "big") % capacity
            if candidate not in used:
                used.add(candidate)
                yield candidate
    raise LSBWatermarkError("V1 位置采样超过预算。")


def _index(position: int, channels: int) -> int:
    pixel, channel = divmod(position, 3)
    return pixel * channels + channel


def _read(
    pixels: bytearray,
    positions: Iterator[int],
    length: int,
    repetition: int,
    channels: int,
) -> bytes:
    result = bytearray()
    try:
        for _ in range(length):
            value = 0
            for _ in range(8):
                ones = sum(
                    pixels[_index(next(positions), channels)] & 1
                    for _ in range(repetition)
                )
                value = (value << 1) | int(ones > repetition // 2)
            result.append(value)
    except (StopIteration, RuntimeError) as exc:
        raise LSBWatermarkError("图片容量不足。") from exc
    return bytes(result)


def embed_lsb(
    png_data: bytes, payload: bytes, *, key: bytes, repetition: int = 3
) -> bytes:
    """写入加密凭证字节；非 IDAT 块原样保留，Alpha 不变。"""
    _validate(repetition)
    auth_key = _derive(key, b"authentication")
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_PAYLOAD_BYTES:
        raise LSBWatermarkError("像素层载荷必须为 1..8192 字节。")
    deadline = time.monotonic() + MAX_SECONDS
    chunks, width, height, mode, pixels = _load(png_data)
    prefix = struct.pack(">4sBBI", b"ODYL", 2, repetition, len(payload))
    tag = hmac.digest(auth_key, prefix + payload, "sha256")[:16]
    packet = prefix + tag + payload
    capacity = width * height * 3
    if len(packet) * 8 * repetition > capacity:
        raise LSBWatermarkError("图片容量不足。")
    # 重新发行时清空所有 RGB 低位，避免未知旧密钥的有效凭证残留。
    # 每个色值仍最多改变 1，Alpha 与其余七位不变。
    clear_lsb = bytes(value & 254 for value in range(256))
    for channel in range(3):
        pixels[channel :: len(mode)] = pixels[channel :: len(mode)].translate(clear_lsb)
    _check_time(deadline)
    positions = _positions(capacity, key, width, height, deadline)
    for value in packet:
        for shift in range(7, -1, -1):
            for _ in range(repetition):
                index = _index(next(positions), len(mode))
                pixels[index] = (pixels[index] & 254) | ((value >> shift) & 1)
    from PIL import Image

    buffer = io.BytesIO()
    Image.frombytes(mode, (width, height), bytes(pixels)).save(
        buffer, format="PNG", compress_level=6
    )
    _check_time(deadline)
    new_chunks = parse_png(buffer.getvalue())
    if new_chunks[0] != chunks[0]:
        raise LSBWatermarkError("编码器改变了 PNG 格式。")
    idats = [c for c in new_chunks if c.chunk_type == b"IDAT"]
    output: list[PNGChunk] = []
    inserted = False
    for chunk in chunks:
        if chunk.chunk_type == b"IDAT":
            if not inserted:
                output.extend(idats)
                inserted = True
        else:
            output.append(chunk)
    result = serialize_png(output)
    if len(result) > MAX_INPUT_BYTES:
        raise LSBWatermarkError("编码结果超过 PNG 输入上限。")
    return result


def extract_lsb_all(
    png_data: bytes,
    *,
    key: bytes | None = None,
    keys: Sequence[bytes] = (),
    repetition: int = 3,
    legacy_v1: bool = False,
) -> list[bytes]:
    """单次解码与共享十秒预算下返回所有不同凭证，避免密钥顺序隐藏冲突。

    V1 必须显式启用且不提供密码学认证。找不到时仍抛出 ExtractionNotFound。
    """
    _validate(repetition)
    if not isinstance(keys, (tuple, list)) or len(keys) + (key is not None) > MAX_KEYS:
        raise LSBWatermarkError("最多允许八把候选密钥。")
    candidates: list[bytes | None] = ([key] if key is not None else []) + list(keys)
    if not candidates:
        if legacy_v1:
            candidates = [None]
        else:
            raise LSBWatermarkError("V2 提取必须提供密钥。")
    if not legacy_v1:
        for candidate in candidates:
            _derive(candidate, b"authentication")  # type: ignore[arg-type]
    elif any(
        candidate is not None
        and (not isinstance(candidate, bytes) or len(candidate) > 1024)
        for candidate in candidates
    ):
        raise LSBWatermarkError("V1 密钥无效。")
    deadline = time.monotonic() + MAX_SECONDS
    _, width, height, mode, pixels = _load(png_data)
    capacity = width * height * 3
    saw_header = False
    recovered: list[bytes] = []
    for candidate in candidates:
        _check_time(deadline)
        header_size = _OLD_HEADER.size if legacy_v1 else _HEADER.size
        if header_size * 8 * repetition > capacity:
            raise LSBWatermarkError("图片容量不足。")
        if legacy_v1:
            positions = _legacy_positions(capacity, candidate, width, height, deadline)
        else:
            assert candidate is not None
            positions = _positions(capacity, candidate, width, height, deadline)
        header = _read(pixels, positions, header_size, repetition, len(mode))
        if legacy_v1:
            magic, version, length, crc = _OLD_HEADER.unpack(header)
            if magic != b"ODYL" or version != 1:
                continue
        else:
            magic, version, stored_rep, length, tag = _HEADER.unpack(header)
            if magic != b"ODYL" or version != 2 or stored_rep != repetition:
                continue
        saw_header = True
        if (
            not 0 < length <= MAX_PAYLOAD_BYTES
            or (header_size + length) * 8 * repetition > capacity
        ):
            continue
        payload = _read(pixels, positions, length, repetition, len(mode))
        if legacy_v1:
            valid = zlib.crc32(payload) & 0xFFFFFFFF == crc
        else:
            expected = hmac.digest(_derive(candidate, b"authentication"), header[:10] + payload, "sha256")[:16]  # type: ignore[arg-type]
            valid = hmac.compare_digest(expected, tag)
        _check_time(deadline)
        if valid and payload not in recovered:
            recovered.append(payload)
    if recovered:
        return recovered
    if not saw_header:
        raise ExtractionNotFound("候选密钥下未找到像素水印。")
    raise LSBWatermarkError("像素水印长度无效或认证失败。")


def extract_lsb(
    png_data: bytes,
    *,
    key: bytes | None = None,
    keys: Sequence[bytes] = (),
    repetition: int = 3,
    legacy_v1: bool = False,
) -> bytes:
    """兼容单凭证接口；候选密钥发现不同凭证时拒绝含糊结果。"""
    payloads = extract_lsb_all(
        png_data, key=key, keys=keys, repetition=repetition, legacy_v1=legacy_v1
    )
    if len(payloads) != 1:
        raise LSBWatermarkError("多个像素水印凭证冲突。")
    return payloads[0]
