"""SillyTavern PNG 角色卡的双层加密溯源核心。

水印同时写入：
1. Character Card JSON 的 ``data.extensions.odysseia_trace``；
2. PNG 私有 ancillary chunk ``trAc``。

PNG 像素数据不会被解码或重新编码，原有 IDAT 字节保持不变。
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import secrets
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TRACE_CHUNK_TYPE = b"trAc"
TRACE_EXTENSION_KEY = "odysseia_trace"
CARD_TEXT_KEYS = {"chara", "ccv3"}
PROTOCOL_VERSION = 1
AAD_PREFIX = b"odysseia-protect-trace"


class WatermarkError(ValueError):
    """水印处理失败。"""


class PNGFormatError(WatermarkError):
    """PNG 结构或 CRC 无效。"""


class VerificationError(WatermarkError):
    """水印无法通过密码学或上下文校验。"""


@dataclass(frozen=True)
class PNGChunk:
    chunk_type: bytes
    data: bytes


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise VerificationError("溯源凭证包含无效的 Base64URL 数据。") from exc


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def parse_key(value: str) -> bytes:
    key = _b64decode(value.strip())
    if len(key) != 32:
        raise WatermarkError("AES-256-GCM 密钥解码后必须正好为 32 字节。")
    return key


def parse_png(data: bytes) -> list[PNGChunk]:
    if not data.startswith(PNG_SIGNATURE):
        raise PNGFormatError("文件不是有效的 PNG。")

    chunks: list[PNGChunk] = []
    position = len(PNG_SIGNATURE)
    seen_iend = False

    while position < len(data):
        if len(data) - position < 12:
            raise PNGFormatError("PNG 尾部存在不完整的 Chunk。")

        length = struct.unpack(">I", data[position : position + 4])[0]
        end = position + 12 + length
        if end > len(data):
            raise PNGFormatError("PNG Chunk 声明长度超出文件边界。")

        chunk_type = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + length]
        stored_crc = struct.unpack(">I", data[position + 8 + length : end])[0]
        actual_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if stored_crc != actual_crc:
            name = chunk_type.decode("latin-1", errors="replace")
            raise PNGFormatError(f"PNG Chunk {name} 的 CRC 校验失败。")

        chunks.append(PNGChunk(chunk_type, payload))
        position = end

        if chunk_type == b"IEND":
            seen_iend = True
            break

    if not chunks or chunks[0].chunk_type != b"IHDR":
        raise PNGFormatError("PNG 缺少首个 IHDR Chunk。")
    if not seen_iend:
        raise PNGFormatError("PNG 缺少 IEND Chunk。")
    if position != len(data):
        raise PNGFormatError("IEND 后存在额外数据。")
    if not any(chunk.chunk_type == b"IDAT" for chunk in chunks):
        raise PNGFormatError("PNG 缺少 IDAT 像素数据。")
    return chunks


def build_chunk(chunk: PNGChunk) -> bytes:
    crc = zlib.crc32(chunk.chunk_type + chunk.data) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(chunk.data))
        + chunk.chunk_type
        + chunk.data
        + struct.pack(">I", crc)
    )


def serialize_png(chunks: Iterable[PNGChunk]) -> bytes:
    return PNG_SIGNATURE + b"".join(build_chunk(chunk) for chunk in chunks)


def parse_text_chunk(chunk: PNGChunk) -> tuple[str, bytes] | None:
    if chunk.chunk_type != b"tEXt" or b"\0" not in chunk.data:
        return None
    keyword, value = chunk.data.split(b"\0", 1)
    try:
        return keyword.decode("latin-1"), value
    except UnicodeDecodeError as exc:
        raise PNGFormatError("tEXt Chunk 关键字无效。") from exc


def build_text_chunk(keyword: str, value: bytes) -> PNGChunk:
    encoded_keyword = keyword.encode("latin-1")
    if not 1 <= len(encoded_keyword) <= 79 or b"\0" in encoded_keyword:
        raise WatermarkError("PNG tEXt 关键字必须为 1 到 79 字节。")
    return PNGChunk(b"tEXt", encoded_keyword + b"\0" + value)


def decode_card_json(value: bytes, keyword: str) -> dict[str, Any]:
    try:
        decoded = base64.b64decode(value, validate=True)
        result = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PNGFormatError(f"{keyword} 中的角色卡 JSON 无效。") from exc
    if not isinstance(result, dict):
        raise PNGFormatError(f"{keyword} 中的角色卡 JSON 必须是对象。")
    return result


def encode_card_json(value: dict[str, Any]) -> bytes:
    return base64.b64encode(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def card_documents(chunks: Iterable[PNGChunk]) -> list[tuple[int, str, dict[str, Any]]]:
    # ponytail: 当前试验只覆盖真实 ST 卡使用的 tEXt/chara 与 tEXt/ccv3；
    # 生产化前若样本出现 zTXt/iTXt，再在这里增加对应解码与原格式回写。
    documents: list[tuple[int, str, dict[str, Any]]] = []
    for index, chunk in enumerate(chunks):
        text = parse_text_chunk(chunk)
        if text is None:
            continue
        keyword, value = text
        if keyword in CARD_TEXT_KEYS:
            documents.append((index, keyword, decode_card_json(value, keyword)))
    return documents


def _extensions(document: dict[str, Any], *, create: bool) -> dict[str, Any] | None:
    data = document.get("data")
    if not isinstance(data, dict):
        if not create:
            return None
        data = {}
        document["data"] = data

    extensions = data.get("extensions")
    if not isinstance(extensions, dict):
        if not create:
            return None
        extensions = {}
        data["extensions"] = extensions
    return extensions


def clean_card_document(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    extensions = _extensions(result, create=False)
    if extensions is not None:
        extensions.pop(TRACE_EXTENSION_KEY, None)
    return result


def _aad(key_id: str) -> bytes:
    return AAD_PREFIX + b"\0" + str(PROTOCOL_VERSION).encode() + b"\0" + key_id.encode()


def create_envelope(payload: dict[str, Any], key: bytes, key_id: str) -> dict[str, Any]:
    if len(key) != 32:
        raise WatermarkError("AES-256-GCM 密钥必须为 32 字节。")
    if not key_id or len(key_id.encode("utf-8")) > 32:
        raise WatermarkError("key_id 必须为 1 到 32 个 UTF-8 字节。")

    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, canonical_json(payload), _aad(key_id))
    return {
        "v": PROTOCOL_VERSION,
        "kid": key_id,
        "nonce": _b64encode(nonce),
        "ciphertext": _b64encode(ciphertext),
    }


def decrypt_envelope(envelope: dict[str, Any], keys: dict[str, bytes]) -> dict[str, Any]:
    if envelope.get("v") != PROTOCOL_VERSION:
        raise VerificationError("不支持的溯源协议版本。")
    key_id = envelope.get("kid")
    if not isinstance(key_id, str) or key_id not in keys:
        raise VerificationError(f"缺少密钥版本：{key_id!r}。")

    try:
        nonce = _b64decode(envelope["nonce"])
        ciphertext = _b64decode(envelope["ciphertext"])
    except KeyError as exc:
        raise VerificationError("溯源信封字段不完整。") from exc
    if len(nonce) != 12:
        raise VerificationError("AES-GCM Nonce 长度无效。")

    try:
        plaintext = AESGCM(keys[key_id]).decrypt(
            nonce, ciphertext, _aad(key_id)
        )
    except InvalidTag as exc:
        raise VerificationError("AES-GCM 认证失败：密钥错误或凭证已被篡改。") from exc

    try:
        payload = json.loads(plaintext)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("解密后的溯源载荷不是有效 JSON。") from exc
    if not isinstance(payload, dict) or payload.get("v") != PROTOCOL_VERSION:
        raise VerificationError("解密后的溯源载荷版本无效。")
    return payload


def _envelopes_from_chunks(
    chunks: list[PNGChunk],
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    found: list[tuple[str, dict[str, Any]]] = []
    card_keys: list[str] = []

    for chunk in chunks:
        if chunk.chunk_type == TRACE_CHUNK_TYPE:
            try:
                envelope = json.loads(chunk.data)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VerificationError("trAc Chunk 中的溯源信封无效。") from exc
            if not isinstance(envelope, dict):
                raise VerificationError("trAc Chunk 中的溯源信封必须是对象。")
            found.append(("png:trAc", envelope))

    for _, keyword, document in card_documents(chunks):
        card_keys.append(keyword)
        extensions = _extensions(document, create=False)
        if extensions is None or TRACE_EXTENSION_KEY not in extensions:
            continue
        envelope = extensions[TRACE_EXTENSION_KEY]
        if not isinstance(envelope, dict):
            raise VerificationError(f"{keyword} 的溯源扩展必须是对象。")
        found.append((f"json:{keyword}", envelope))
    return found, card_keys


def inject_watermark(
    png_data: bytes,
    *,
    key: bytes,
    key_id: str,
    user_id: str,
    card_id: str,
    resource_id: str,
    issued_at: int | None = None,
    delivery_id: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    chunks = parse_png(png_data)
    documents = card_documents(chunks)
    if not documents:
        raise WatermarkError("PNG 中没有找到 tEXt/chara 或 tEXt/ccv3 角色卡数据。")
    if not user_id or not card_id or not resource_id:
        raise WatermarkError("user_id、card_id 和 resource_id 均不能为空。")

    payload = {
        "v": PROTOCOL_VERSION,
        "uid": str(user_id),
        "card_id": str(card_id),
        "resource_id": str(resource_id),
        "issued_at": int(time.time()) if issued_at is None else int(issued_at),
        "delivery_id": delivery_id or _b64encode(secrets.token_bytes(16)),
    }
    envelope = create_envelope(payload, key, key_id)
    envelope_bytes = canonical_json(envelope)

    updated_documents: dict[int, PNGChunk] = {}
    for index, keyword, document in documents:
        clean = clean_card_document(document)
        extensions = _extensions(clean, create=True)
        assert extensions is not None
        extensions[TRACE_EXTENSION_KEY] = envelope
        updated_documents[index] = build_text_chunk(keyword, encode_card_json(clean))

    output_chunks: list[PNGChunk] = []
    for index, chunk in enumerate(chunks):
        if chunk.chunk_type == TRACE_CHUNK_TYPE:
            continue
        if index in updated_documents:
            chunk = updated_documents[index]
        if chunk.chunk_type == b"IEND":
            output_chunks.append(PNGChunk(TRACE_CHUNK_TYPE, envelope_bytes))
        output_chunks.append(chunk)

    return serialize_png(output_chunks), payload


def verify_watermark(
    png_data: bytes,
    *,
    keys: dict[str, bytes],
    expected_card_id: str | None = None,
    strict_layers: bool = False,
) -> dict[str, Any]:
    chunks = parse_png(png_data)
    found, card_keys = _envelopes_from_chunks(chunks)
    if not found:
        raise VerificationError("文件中没有找到溯源凭证。")

    by_content: dict[bytes, list[str]] = {}
    envelopes: dict[bytes, dict[str, Any]] = {}
    for source, envelope in found:
        encoded = canonical_json(envelope)
        by_content.setdefault(encoded, []).append(source)
        envelopes[encoded] = envelope
    if len(by_content) != 1:
        sources = [source for group in by_content.values() for source in group]
        raise VerificationError(f"不同水印层包含冲突凭证：{', '.join(sources)}。")

    encoded, sources = next(iter(by_content.items()))
    payload = decrypt_envelope(envelopes[encoded], keys)
    if expected_card_id is not None and payload.get("card_id") != expected_card_id:
        raise VerificationError(
            f"Card ID 不匹配：凭证={payload.get('card_id')!r}，"
            f"预期={expected_card_id!r}。"
        )

    expected_sources = {"png:trAc", *(f"json:{key}" for key in card_keys)}
    missing_sources = sorted(expected_sources - set(sources))
    if strict_layers and missing_sources:
        raise VerificationError(f"严格校验缺少水印层：{', '.join(missing_sources)}。")

    return {
        "valid": True,
        "payload": payload,
        "key_id": envelopes[encoded]["kid"],
        "sources": sorted(sources),
        "missing_sources": missing_sources,
        "layer_status": "complete" if not missing_sources else "degraded",
    }


def inspect_png(png_data: bytes) -> dict[str, Any]:
    chunks = parse_png(png_data)
    documents = card_documents(chunks)
    idat = b"".join(chunk.data for chunk in chunks if chunk.chunk_type == b"IDAT")
    cards = []
    for _, keyword, document in documents:
        cards.append(
            {
                "keyword": keyword,
                "name": document.get("name") or document.get("data", {}).get("name"),
                "spec": document.get("spec"),
                "spec_version": document.get("spec_version"),
                "json_sha256": hashlib.sha256(canonical_json(document)).hexdigest(),
                "has_trace": bool(
                    (_extensions(document, create=False) or {}).get(TRACE_EXTENSION_KEY)
                ),
            }
        )
    return {
        "size": len(png_data),
        "sha256": hashlib.sha256(png_data).hexdigest(),
        "idat_sha256": hashlib.sha256(idat).hexdigest(),
        "chunks": [
            {"type": chunk.chunk_type.decode("latin-1"), "size": len(chunk.data)}
            for chunk in chunks
        ],
        "cards": cards,
    }


def _write_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("keygen", help="生成一个 AES-256-GCM Base64URL 密钥")

    inspect_parser = subparsers.add_parser("inspect", help="检查 PNG 与角色卡结构")
    inspect_parser.add_argument("input", type=Path)

    inject_parser = subparsers.add_parser("inject", help="向角色卡注入双层溯源凭证")
    inject_parser.add_argument("input", type=Path)
    inject_parser.add_argument("output", type=Path)
    inject_parser.add_argument(
        "--key", help="仅供实验；省略时读取 ODYSSEIA_TRACE_KEY"
    )
    inject_parser.add_argument("--key-id", required=True)
    inject_parser.add_argument("--user-id", required=True)
    inject_parser.add_argument("--card-id", required=True)
    inject_parser.add_argument("--resource-id", required=True)
    inject_parser.add_argument("--issued-at", type=int)
    inject_parser.add_argument("--delivery-id")

    verify_parser = subparsers.add_parser("verify", help="提取、解密并校验溯源凭证")
    verify_parser.add_argument("input", type=Path)
    verify_parser.add_argument(
        "--key", help="仅供实验；省略时读取 ODYSSEIA_TRACE_KEY"
    )
    verify_parser.add_argument("--expected-card-id")
    verify_parser.add_argument("--strict-layers", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "keygen":
            print(_b64encode(secrets.token_bytes(32)))
            return 0

        png_data = args.input.read_bytes()
        if args.command == "inspect":
            _write_json(inspect_png(png_data))
            return 0

        key_value = args.key or os.getenv("ODYSSEIA_TRACE_KEY")
        if not key_value:
            raise WatermarkError(
                "请通过 ODYSSEIA_TRACE_KEY 环境变量或 --key 提供密钥。"
            )
        key = parse_key(key_value)
        if args.command == "inject":
            output, payload = inject_watermark(
                png_data,
                key=key,
                key_id=args.key_id,
                user_id=args.user_id,
                card_id=args.card_id,
                resource_id=args.resource_id,
                issued_at=args.issued_at,
                delivery_id=args.delivery_id,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(output)
            _write_json(
                {
                    "output": str(args.output),
                    "payload": payload,
                    "before": inspect_png(png_data),
                    "after": inspect_png(output),
                }
            )
            return 0

        inspection = inspect_png(png_data)
        found, _ = _envelopes_from_chunks(parse_png(png_data))
        if not found:
            raise VerificationError("文件中没有找到溯源凭证。")
        key_ids = {envelope.get("kid") for _, envelope in found}
        if len(key_ids) != 1 or not all(isinstance(item, str) for item in key_ids):
            raise VerificationError("无法确定唯一有效的密钥版本。")
        key_id = next(iter(key_ids))
        result = verify_watermark(
            png_data,
            keys={key_id: key},
            expected_card_id=args.expected_card_id,
            strict_layers=args.strict_layers,
        )
        result["file"] = inspection
        _write_json(result)
        return 0
    except (OSError, WatermarkError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
