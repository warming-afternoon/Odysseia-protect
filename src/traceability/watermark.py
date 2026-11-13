"""角色卡溯源：安全宏注释、PNG trAc 与旧版扩展兼容读取。"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import os
import re
import secrets
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TRACE_CHUNK_TYPE = b"trAc"
TRACE_EXTENSION_KEY = "odysseia_trace"
CARD_TEXT_KEYS = {"chara", "ccv3"}
MACRO_FIELDS = ("mes_example", "personality", "scenario")
MACRO_RE = re.compile(r"\{\{//(?P<label>[0-9a-f]{16}):(?P<encoded>[^{}]*)\}\}")
MAX_MACRO_CANDIDATES = 128
MAX_MACRO_ENCODED_CHARS = 32 * 1024
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
    if not isinstance(value, str):
        raise VerificationError("溯源凭证 Base64URL 字段必须是字符串。")
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


def _card_scopes(document: dict[str, Any]) -> list[dict[str, Any]]:
    """仅处理规范主体和已存在的顶层兼容镜像。"""
    data = document.get("data")
    return [data, document] if isinstance(data, dict) else [document]


def _document_macros(
    document: dict[str, Any],
) -> Iterable[tuple[dict[str, Any], str, re.Match[str], dict[str, Any] | None]]:
    """按统一格式扫描注释候选并限制单文档解析成本。"""
    count = 0
    for scope in _card_scopes(document):
        for field in MACRO_FIELDS:
            value = scope.get(field)
            if not isinstance(value, str):
                continue
            for match in MACRO_RE.finditer(value):
                count += 1
                if count > MAX_MACRO_CANDIDATES:
                    raise VerificationError("单份 JSON 的注释候选超过 128 个。")
                encoded = match.group("encoded")
                if len(encoded) > MAX_MACRO_ENCODED_CHARS:
                    raise VerificationError("单个注释编码凭证超过 32 KiB。")
                try:
                    envelope = json.loads(_b64decode(encoded))
                except (WatermarkError, ValueError, UnicodeDecodeError, RecursionError):
                    envelope = None
                else:
                    # 可解码但无信封字段的普通注释不作为水印层报告。
                    if not isinstance(envelope, dict) or not any(
                        name in envelope for name in ("v", "kid", "nonce", "ciphertext")
                    ):
                        continue
                # 仅移除结构完整的信封；损坏候选保留在正文中供摘要核对。
                if not (
                    isinstance(envelope, dict)
                    and type(envelope.get("v")) is int
                    and all(
                        isinstance(envelope.get(name), str) and envelope[name]
                        for name in ("kid", "nonce", "ciphertext")
                    )
                ):
                    envelope = None
                yield scope, field, match, envelope


def clean_card_document(document: dict[str, Any]) -> dict[str, Any]:
    """移除可识别的注释凭证和正式服旧扩展，保留其他内容。"""
    result = copy.deepcopy(document)
    macros = list(_document_macros(result))
    # 从后向前删除，避免同一字段中前面的字符位置发生变化。
    for scope, field, match, envelope in reversed(macros):
        if envelope is not None:
            value = scope[field]
            scope[field] = value[:match.start()] + value[match.end():]
    for scope in _card_scopes(result):
        extensions = scope.get("extensions")
        if isinstance(extensions, dict):
            extensions.pop(TRACE_EXTENSION_KEY, None)
    return result


def card_content_sha256(document: dict[str, Any], *, version: int = 2) -> str:
    """按摘要版本规范化已确认的客户端导出差异，保持正文严格绑定。"""
    if type(version) is not int or version not in (1, 2):
        raise VerificationError("不支持的内容摘要版本。")
    result = clean_card_document(document)
    if version == 1:
        return hashlib.sha256(canonical_json(result)).hexdigest()
    result.pop("create_date", None)
    data = result.get("data")
    scopes = [result]
    if isinstance(data, dict):
        extensions = data.get("extensions")
        if isinstance(extensions, dict):
            scopes.append(extensions)
        book = data.get("character_book")
        if isinstance(book, dict) and book.get("extensions") == {}:
            book.pop("extensions")
    for scope in scopes:
        value = scope.get("talkativeness")
        # 仅统一有限的活跃度数值，布尔值及其他类型仍按原样绑定。
        if type(value) in (int, float, str):
            if isinstance(value, str) and len(value) > 128:
                continue
            try:
                number = float(value)
                # 不把超出浮点精度的字符串舍入成另一份内容。
                exact = Decimal(str(value)) == Decimal(str(number))
            except (ValueError, OverflowError, InvalidOperation):
                continue
            if exact and math.isfinite(number) and 0 <= number <= 1:
                scope["talkativeness"] = number
    return hashlib.sha256(canonical_json(result)).hexdigest()


def _safe_macro_text(value: Any) -> bool:
    # 模板、未闭合宏、脚本及命令可能改变注释解析，保守跳过。
    return (
        isinstance(value, str)
        and not any(
            token in value
            for token in ("{{", "}}", "<%", "%>", "${", "<script", "\x00")
        )
        and not value.lstrip().startswith("/")
    )


def _choose_macro_field(
    documents: list[dict[str, Any]], *, allow_empty_fields: bool = True
) -> str | None:
    candidates: list[str] = []
    empty: list[str] = []
    for field in MACRO_FIELDS:
        values: list[str] = []
        safe = True
        for document in documents:
            scopes = _card_scopes(document)
            # 不创建缺失字段，也不把对象或数组强制转换成字符串。
            if field not in scopes[0]:
                safe = False
            if (
                len(scopes) == 2
                and field in scopes[1]
                and scopes[0].get(field) != scopes[1][field]
            ):
                safe = False
            for scope in scopes:
                if field in scope:
                    value = scope[field]
                    if not _safe_macro_text(value):
                        safe = False
                    else:
                        values.append(value)
        if (
            safe
            and values
            and (allow_empty_fields or all(value.strip() for value in values))
        ):
            candidates.append(field)
            if all(value.strip() == "" for value in values):
                empty.append(field)
    choices = empty or candidates
    return secrets.choice(choices) if choices else None


def _mark_document(
    document: dict[str, Any], envelope: dict[str, Any], field: str | None, label: str
) -> dict[str, Any]:
    result = clean_card_document(document)
    if field is not None:
        encoded = _b64encode(canonical_json(envelope))
        if len(encoded) > MAX_MACRO_ENCODED_CHARS:
            raise WatermarkError("单个注释编码凭证超过 32 KiB。")
        macro = "{{//" + label + ":" + encoded + "}}"
        for scope in _card_scopes(result):
            if field in scope:
                # 必须置于尾部空白之前，保持客户端先 trim 后解析宏的结果。
                value = scope[field]
                end = len(value.rstrip())
                scope[field] = value[:end] + macro + value[end:]
    # 新增凭证和已有镜像也计入上限，避免交付无法被自身核验的文件。
    list(_document_macros(result))
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


def decrypt_envelope(
    envelope: dict[str, Any], keys: dict[str, bytes]
) -> dict[str, Any]:
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
        plaintext = AESGCM(keys[key_id]).decrypt(nonce, ciphertext, _aad(key_id))
    except InvalidTag as exc:
        raise VerificationError("AES-GCM 认证失败：密钥错误或凭证已被篡改。") from exc

    try:
        payload = json.loads(plaintext)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("解密后的溯源载荷不是有效 JSON。") from exc
    if not isinstance(payload, dict) or payload.get("v") != PROTOCOL_VERSION:
        raise VerificationError("解密后的溯源载荷版本无效。")
    return payload


def _document_layers(document: dict[str, Any], source: str) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    for scope in _card_scopes(document):
        extensions = scope.get("extensions")
        if isinstance(extensions, dict) and TRACE_EXTENSION_KEY in extensions:
            found.append(
                (source.replace("json:", "legacy:"), extensions[TRACE_EXTENSION_KEY])
            )
    for _, _, _, envelope in _document_macros(document):
        found.append((source.replace("json:", "macro:"), envelope))
    return found


def _collect_layers(
    data: bytes,
) -> tuple[list[tuple[str, Any]], list[str], list[dict[str, Any]]]:
    found: list[tuple[str, Any]] = []
    expected: list[str] = []
    documents: list[dict[str, Any]] = []
    if not data.startswith(PNG_SIGNATURE):
        try:
            document = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise VerificationError("输入不是有效的 PNG 或角色卡 JSON。") from exc
        if not isinstance(document, dict):
            raise VerificationError("角色卡 JSON 必须是对象。")
        return (
            _document_layers(document, "json:standalone"),
            ["json:standalone"],
            [document],
        )
    chunks = parse_png(data)
    expected.append("png:trAc")
    for chunk in chunks:
        if chunk.chunk_type == TRACE_CHUNK_TYPE:
            try:
                envelope = json.loads(chunk.data)
            except (ValueError, UnicodeDecodeError):
                envelope = None
            found.append(("png:trAc", envelope))
        text = parse_text_chunk(chunk)
        if text is not None and text[0] in CARD_TEXT_KEYS:
            source = "json:" + text[0]
            expected.append(source)
            try:
                document = decode_card_json(text[1], text[0])
            except PNGFormatError:
                found.append((source, None))
                continue
            documents.append(document)
            found.extend(_document_layers(document, source))
    return found, expected, documents


def _envelopes_from_chunks(
    chunks: list[PNGChunk],
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    found, expected, _ = _collect_layers(serialize_png(chunks))
    return [
        (source, envelope) for source, envelope in found if isinstance(envelope, dict)
    ], [source[5:] for source in expected if source.startswith("json:")]


def _new_payload(
    *,
    user_id: str,
    card_id: str,
    resource_id: str,
    issued_at: int | None,
    delivery_id: str | None,
    author_id: str | None,
) -> dict[str, Any]:
    """生成独立于正文内容的加密签发信息。"""
    if not user_id or not card_id or not resource_id:
        raise WatermarkError("user_id、card_id 和 resource_id 均不能为空。")
    return {
        "v": PROTOCOL_VERSION,
        "uid": str(user_id),
        "card_id": str(card_id),
        "resource_id": str(resource_id),
        "author_id": str(author_id) if author_id is not None else None,
        "issued_at": int(time.time()) if issued_at is None else int(issued_at),
        "delivery_id": delivery_id or _b64encode(secrets.token_bytes(16)),
    }


def inject_json_watermark(
    json_data: bytes,
    *,
    key: bytes,
    key_id: str,
    user_id: str,
    card_id: str,
    resource_id: str,
    issued_at: int | None = None,
    delivery_id: str | None = None,
    allow_empty_fields: bool = True,
    author_id: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    _, _, documents = _collect_layers(json_data)
    if json_data.startswith(PNG_SIGNATURE):
        raise WatermarkError("此接口仅接受独立 JSON 角色卡。")
    clean = [clean_card_document(documents[0])]
    field = _choose_macro_field(clean, allow_empty_fields=allow_empty_fields)
    if field is None:
        raise WatermarkError("没有可安全写入宏注释的字段。")
    payload = _new_payload(
        user_id=user_id,
        card_id=card_id,
        resource_id=resource_id,
        issued_at=issued_at,
        delivery_id=delivery_id,
        author_id=author_id,
    )
    payload["macro_field"] = field
    payload["macro_status"] = "inserted" if field else "skipped_unsafe_or_empty"
    payload["allow_empty_fields"] = allow_empty_fields
    payload["carriers"] = ["macro"] if field else []
    envelope = create_envelope(payload, key, key_id)
    marked = _mark_document(clean[0], envelope, field, secrets.token_hex(8))
    return canonical_json(marked), payload


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
    expected_extra_sources: Iterable[str] = (),
    allow_empty_fields: bool = True,
    author_id: str | None = None,
    pixel_layer: dict[str, str] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    chunks = parse_png(png_data)
    documents = card_documents(chunks)
    if not documents:
        raise WatermarkError("PNG 中没有找到 tEXt/chara 或 tEXt/ccv3 角色卡数据。")
    clean = [clean_card_document(document) for _, _, document in documents]
    field = _choose_macro_field(clean, allow_empty_fields=allow_empty_fields)
    payload = _new_payload(
        user_id=user_id,
        card_id=card_id,
        resource_id=resource_id,
        issued_at=issued_at,
        delivery_id=delivery_id,
        author_id=author_id,
    )
    payload["macro_field"] = field
    payload["macro_status"] = "inserted" if field else "skipped_unsafe_or_empty"
    payload["allow_empty_fields"] = allow_empty_fields
    payload["carriers"] = [
        "png:trAc",
        *(["macro"] if field else []),
        *expected_extra_sources,
    ]
    payload["macro_sources"] = (
        sorted({"json:" + keyword for _, keyword, _ in documents}) if field else []
    )
    payload["pixel_layer"] = dict(pixel_layer) if pixel_layer is not None else {
        "status": "embedded" if "pixel:lsb" in payload["carriers"] else "disabled"
    }
    envelope = create_envelope(payload, key, key_id)
    label = secrets.token_hex(8)
    updated = {
        index: build_text_chunk(
            keyword, encode_card_json(_mark_document(document, envelope, field, label))
        )
        for index, keyword, document in documents
    }
    output: list[PNGChunk] = []
    for index, chunk in enumerate(chunks):
        if chunk.chunk_type == TRACE_CHUNK_TYPE:
            continue
        if chunk.chunk_type == b"IEND":
            output.append(PNGChunk(TRACE_CHUNK_TYPE, canonical_json(envelope)))
        output.append(updated.get(index, chunk))
    return serialize_png(output), payload


def verify_watermark(
    png_data: bytes,
    *,
    keys: dict[str, bytes],
    expected_card_id: str | None = None,
    expected_resource_id: str | None = None,
    expected_content_sha256: str | None = None,
    strict_layers: bool = False,
    extra_envelopes: Iterable[tuple[str, Any]] = (),
    extra_layer_results: Iterable[dict[str, str]] = (),
) -> dict[str, Any]:
    found, expected, documents = _collect_layers(png_data)
    found.extend(extra_envelopes)
    if not found:
        raise VerificationError("文件中没有找到溯源凭证。")
    authenticated: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    statuses: list[dict[str, str]] = list(extra_layer_results)
    for source, envelope in found:
        if not isinstance(envelope, dict):
            statuses.append({"source": source, "status": "malformed"})
            continue
        try:
            payload = decrypt_envelope(envelope, keys)
        except (WatermarkError, ValueError, TypeError) as exc:
            statuses.append(
                {"source": source, "status": "decryption_failed", "error": str(exc)}
            )
            continue
        authenticated.append((source, envelope, payload))
        statuses.append({"source": source, "status": "authenticated"})
    if not authenticated:
        errors = "; ".join(item.get("error", "格式无效") for item in statuses)
        raise VerificationError("没有可认证的溯源凭证：" + errors)
    if len({canonical_json(item[2]) for item in authenticated}) != 1:
        raise VerificationError("不同水印层包含冲突凭证。")
    payload = authenticated[0][2]
    for name, expected_value, label in (
        ("card_id", expected_card_id, "Card ID"),
        ("resource_id", expected_resource_id, "Resource ID"),
    ):
        if expected_value is not None and payload.get(name) != expected_value:
            raise VerificationError(
                f"{label} 不匹配：凭证={payload.get(name)!r}，预期={expected_value!r}。"
            )
    bound = payload.get("content_sha256")
    if expected_content_sha256 is not None and (
        not isinstance(bound, list) or expected_content_sha256 not in bound
    ):
        raise VerificationError("可信内容摘要不匹配或旧凭证未绑定内容。")
    # 只认证签发信息，不再用嫌疑样本正文计算摘要或判断内容匹配。
    sources = sorted({source for source, _, _ in authenticated})
    recovered = {
        source.replace("macro:", "json:").replace("legacy:", "json:")
        for source in sources
    }
    carriers = payload.get("carriers")
    if isinstance(carriers, list):
        if "macro" not in carriers:
            expected = [source for source in expected if not source.startswith("json:")]
        if png_data.startswith(PNG_SIGNATURE):
            expected.extend(
                source for source in carriers if source not in ("macro", "png:trAc")
            )
            if "macro" in carriers:
                signed_sources = payload.get("macro_sources")
                if isinstance(signed_sources, list) and all(
                    isinstance(source, str) for source in signed_sources
                ):
                    expected.extend(signed_sources)
                elif not any(source.startswith("json:") for source in expected):
                    # 兼容早期仅签署宏种类、未列出各文档来源的凭证。
                    expected.append("macro")
                    if any(source.startswith("macro:") for source in sources):
                        recovered.add("macro")
    missing = sorted(set(expected) - recovered)
    damaged = any(
        item["status"] != "authenticated"
        and not (
            item["status"] in {"unavailable", "absent", "unsupported"}
            and item["source"] not in expected
        )
        for item in statuses
    )
    if strict_layers and (missing or damaged):
        raise VerificationError("严格校验缺少水印层或存在损坏水印层。")
    return {
        "valid": True,
        "credential_valid": True,
        "content_status": "not_checked",
        "payload": payload,
        "key_id": authenticated[0][1]["kid"],
        "sources": sources,
        "missing_sources": missing,
        "layer_status": "degraded" if missing or damaged else "complete",
        "layer_results": statuses,
        "content_match": None,
        "content_binding": "not_checked",
        "attribution_warning": "凭证仅证明签发记录，不能单独证明泄露行为。",
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
                "has_trace": bool(_document_layers(document, "json:" + keyword)),
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
    inject_parser.add_argument("--key", help="仅供实验；省略时读取 ODYSSEIA_TRACE_KEY")
    inject_parser.add_argument("--key-id", required=True)
    inject_parser.add_argument("--user-id", required=True)
    inject_parser.add_argument("--card-id", required=True)
    inject_parser.add_argument("--resource-id", required=True)
    inject_parser.add_argument("--author-id")
    inject_parser.add_argument("--issued-at", type=int)
    inject_parser.add_argument("--delivery-id")
    inject_parser.add_argument(
        "--allow-empty-fields",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="默认优先空字段；群聊可能多出分隔符或模板标题，可用 --no-allow-empty-fields 关闭",
    )

    verify_parser = subparsers.add_parser("verify", help="提取、解密并校验溯源凭证")
    verify_parser.add_argument("input", type=Path)
    verify_parser.add_argument("--key", help="仅供实验；省略时读取 ODYSSEIA_TRACE_KEY")
    verify_parser.add_argument("--key-id", help="明确指定验证密钥版本")
    verify_parser.add_argument("--expected-card-id")
    verify_parser.add_argument("--expected-resource-id")
    verify_parser.add_argument("--expected-content-sha256")
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
            injector = (
                inject_watermark
                if png_data.startswith(PNG_SIGNATURE)
                else inject_json_watermark
            )
            output, payload = injector(
                png_data,
                key=key,
                key_id=args.key_id,
                user_id=args.user_id,
                card_id=args.card_id,
                resource_id=args.resource_id,
                author_id=args.author_id,
                issued_at=args.issued_at,
                delivery_id=args.delivery_id,
                allow_empty_fields=args.allow_empty_fields,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(output)
            _write_json(
                {
                    "output": str(args.output),
                    "payload": payload,
                    "before": (
                        inspect_png(png_data)
                        if png_data.startswith(PNG_SIGNATURE)
                        else {"format": "json"}
                    ),
                    "after": (
                        inspect_png(output)
                        if output.startswith(PNG_SIGNATURE)
                        else {"format": "json"}
                    ),
                }
            )
            return 0

        inspection = {
            "format": "png" if png_data.startswith(PNG_SIGNATURE) else "json",
            "sha256": hashlib.sha256(png_data).hexdigest(),
        }
        found, _, _ = _collect_layers(png_data)
        # 单密钥可尝试每个声明版本；只有 AES-GCM 认证成功的凭证参与结果。
        key_ids: set[str] = set()
        for _, envelope in found:
            candidate_id = envelope.get("kid") if isinstance(envelope, dict) else None
            if isinstance(candidate_id, str):
                key_ids.add(candidate_id)
        keyring: dict[str, bytes] = (
            {str(args.key_id): key}
            if args.key_id
            else {key_id: key for key_id in key_ids}
        )
        result = verify_watermark(
            png_data,
            keys=keyring,
            expected_card_id=args.expected_card_id,
            expected_resource_id=args.expected_resource_id,
            expected_content_sha256=args.expected_content_sha256,
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
