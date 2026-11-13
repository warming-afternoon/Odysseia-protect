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
import os
import struct
import time
import zlib
from collections.abc import Iterator, Sequence

from .watermark import PNGChunk, WatermarkError, parse_png, serialize_png
from .limits import DEFAULT_MAX_OUTPUT_BYTES, MAX_DECODED_BYTES
from .lsb_skip_error import LSBSkipError
from src.utils.env_config import positive_int

MAX_INPUT_BYTES = DEFAULT_MAX_OUTPUT_BYTES
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
        raise LSBSkipError("time_budget", "LSB 处理超过时间预算。")


def _validate(repetition: int) -> None:
    if type(repetition) is not int or repetition not in (1, 3, 5):
        raise LSBWatermarkError("LSB repetition 必须为 1、3 或 5。")


def _derive(key: bytes, purpose: bytes) -> bytes:
    if not isinstance(key, bytes) or len(key) != 32:
        raise LSBWatermarkError("LSB 必须使用 32 字节 AES 密钥。")
    return hmac.digest(key, _DOMAIN + purpose, "sha256")


def _convert_chunks(
    chunks: list[PNGChunk], depth: int, color: int, mode: str
) -> list[PNGChunk]:
    """转换随颜色模式变化的块，保留角色卡和有效色彩信息。"""
    palette = next((chunk.data for chunk in chunks if chunk.chunk_type == b"PLTE"), b"")
    result = []
    for chunk in chunks:
        kind, value = chunk.chunk_type, chunk.data
        if kind in (b"PLTE", b"tRNS", b"hIST", b"dSIG"):
            continue
        if kind == b"IHDR":
            value = value[:8] + bytes((8, 6 if mode == "RGBA" else 2, 0, 0, 0))
        elif kind == b"bKGD":
            if color == 3:
                if len(value) != 1 or (value[0] + 1) * 3 > len(palette):
                    raise LSBWatermarkError("PNG 调色板背景索引无效。")
                rgb = palette[value[0] * 3 : (value[0] + 1) * 3]
                value = struct.pack(">HHH", *rgb)
            elif color in (0, 4):
                if len(value) != 2:
                    raise LSBWatermarkError("PNG 灰度背景数据无效。")
                grey = int.from_bytes(value, "big")
                if grey >= 1 << depth:
                    raise LSBWatermarkError("PNG 灰度背景数值无效。")
                grey = grey * 255 // ((1 << depth) - 1)
                value = struct.pack(">HHH", grey, grey, grey)
        elif kind == b"sBIT":
            expected = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}[color]
            maximum = 8 if color == 3 else depth
            if len(value) != expected or any(not 0 < bit <= maximum for bit in value):
                raise LSBWatermarkError("PNG 有效位深数据无效。")
            if color in (0, 4):
                value = value[:1] * 3 + (value[1:] if color == 4 else b"")
            if mode == "RGBA" and len(value) == 3:
                value += b"\x08"
        result.append(PNGChunk(kind, value))
    return result


def _load(
    data: bytes, *, convert: bool = False, max_bytes: int | None = None
) -> tuple[list[PNGChunk], int, int, str, bytearray]:
    """先检查格式和内存预算，再解码并按需转换静态八位图像。"""
    # 先检查 IHDR 和压缩输入大小，再允许 Pillow 解码，避免解压炸弹。
    if max_bytes is None:
        max_bytes = positive_int(os.environ, "TRACE_MAX_OUTPUT_BYTES", MAX_INPUT_BYTES)
    if len(data) > max_bytes:
        raise LSBWatermarkError("PNG 超过像素层输入上限。")
    chunks = parse_png(data)
    if len(chunks[0].data) != 13:
        raise LSBWatermarkError("IHDR 长度无效。")
    width, height, depth, color, comp, filt, interlace = struct.unpack(
        ">IIBBBBB", chunks[0].data
    )
    allowed = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if not width or not height or depth not in allowed.get(color, ()) or comp or filt or interlace not in (0, 1):
        raise LSBWatermarkError("PNG 图像头参数无效。")
    if sum(c.chunk_type == b"IHDR" for c in chunks) != 1:
        raise LSBWatermarkError("PNG 存在重复 IHDR。")
    if any(not (c.chunk_type[0] & 32) and c.chunk_type not in (b"IHDR", b"PLTE", b"IDAT", b"IEND") for c in chunks):
        raise LSBWatermarkError("PNG 包含未知的关键块。")
    if any(c.chunk_type in (b"acTL", b"fcTL", b"fdAT") for c in chunks):
        raise LSBSkipError("animation", "动画 PNG 保留原编码，不写入像素层。")
    if depth == 16:
        raise LSBSkipError("bit_depth_16", "16 位 PNG 保留原精度，不写入像素层。")
    if width * height > MAX_PIXELS:
        raise LSBSkipError("pixel_limit", "PNG 像素数超过安全上限。")
    transparency = next((c.data for c in chunks if c.chunk_type == b"tRNS"), None)
    transparent = transparency is not None
    if transparency is not None:
        valid = color == 3 and 0 < len(transparency) <= 256
        if color == 0:
            valid = len(transparency) == 2 and int.from_bytes(transparency, "big") < 1 << depth
        elif color == 2:
            valid = len(transparency) == 6 and all(v <= 255 for v in struct.unpack(">HHH", transparency))
        if not valid:
            raise LSBWatermarkError("PNG 透明色数据无效。")
    mode = "RGBA" if color in (4, 6) or transparent else "RGB"
    if width * height * len(mode) > MAX_DECODED_BYTES:
        raise LSBSkipError("decoded_limit", "解码后像素数据超过 50 MB。")
    needs_conversion = color not in (2, 6) or interlace or transparent
    if needs_conversion and not convert:
        raise LSBSkipError("unsupported_format", "像素提取需要非交错 8-bit RGB/RGBA PNG。")
    if color in (0, 4) and any(c.chunk_type in (b"iCCP", b"cICP") for c in chunks):
        raise LSBSkipError("color_metadata", "无法安全转换灰度色彩配置，保留源图像。")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            if image.size != (width, height):
                raise LSBWatermarkError("PNG 解码模式不一致。")
            if needs_conversion:
                working = image
                if color == 0 and depth < 8 and transparency is not None:
                    # Pillow 已展开灰度样本，但 tRNS 仍使用原位深的数值。
                    working = image.convert("L")
                    working.info["transparency"] = int.from_bytes(transparency, "big") * 255 // ((1 << depth) - 1)
                try:
                    with working.convert(mode) as converted:
                        pixels = bytearray(converted.tobytes())
                finally:
                    if working is not image:
                        working.close()
            else:
                if image.mode != mode:
                    raise LSBWatermarkError("PNG 解码模式不一致。")
                pixels = bytearray(image.tobytes())
    except ImportError as exc:
        raise LSBWatermarkError("像素层需要安装 Pillow 可选依赖。") from exc
    except (OSError, ValueError) as exc:
        raise LSBWatermarkError("PNG 像素解码失败。") from exc
    if needs_conversion:
        chunks = _convert_chunks(chunks, depth, color, mode)
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
    png_data: bytes, payload: bytes, *, key: bytes, repetition: int = 3,
    max_output_bytes: int | None = None,
) -> bytes:
    """按需转换后写入凭证，保留角色卡数据和 Alpha 并同步颜色元数据。"""
    _validate(repetition)
    auth_key = _derive(key, b"authentication")
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_PAYLOAD_BYTES:
        raise LSBWatermarkError("像素层载荷必须为 1..8192 字节。")
    deadline = time.monotonic() + MAX_SECONDS
    if max_output_bytes is None:
        max_output_bytes = positive_int(os.environ, "TRACE_MAX_OUTPUT_BYTES", MAX_INPUT_BYTES)
    chunks, width, height, mode, pixels = _load(png_data, convert=True, max_bytes=max_output_bytes)
    _check_time(deadline)
    prefix = struct.pack(">4sBBI", b"ODYL", 2, repetition, len(payload))
    tag = hmac.digest(auth_key, prefix + payload, "sha256")[:16]
    packet = prefix + tag + payload
    capacity = width * height * 3
    if len(packet) * 8 * repetition > capacity:
        raise LSBSkipError("capacity", "图片容量不足。")
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
    if len(result) > max_output_bytes:
        raise LSBSkipError("output_limit", "编码结果超过交付输出上限。")
    return result


def extract_lsb_all(
    png_data: bytes,
    *,
    key: bytes | None = None,
    keys: Sequence[bytes] = (),
    repetition: int = 3,
    legacy_v1: bool = False,
    max_input_bytes: int | None = None,
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
    _, width, height, mode, pixels = _load(png_data, max_bytes=max_input_bytes)
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
