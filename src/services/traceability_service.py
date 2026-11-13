"""生成不落身份账本的个性化角色卡副本。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from src.utils.env_config import positive_int
from src.traceability.limits import DEFAULT_MAX_OUTPUT_BYTES, MAX_SOURCE_BYTES
from src.traceability.lsb_skip_error import LSBSkipError

from src.traceability.lsb_watermark import (
    ExtractionNotFound,
    LSBWatermarkError,
    embed_lsb,
    extract_lsb_all,
)
from src.traceability.watermark import (
    PNG_SIGNATURE,
    TRACE_CHUNK_TYPE,
    WatermarkError,
    card_documents,
    inject_json_watermark,
    inject_watermark,
    parse_key,
    parse_png,
    verify_watermark,
)

logger = logging.getLogger(__name__)
_TRUE_VALUES = {"1", "true", "yes", "on", "开启", "是"}


class TraceabilityUnavailableError(RuntimeError):
    """服务器未正确配置溯源能力。"""


@dataclass(frozen=True)
class PersonalizedCard:
    """个性化文件及保留原格式的下载名称。"""

    data: bytes

    filename: str


class TraceabilityService:
    """管理当前签发密钥、历史核验密钥与可选像素层。"""

    def __init__(
        self,
        *,
        key: bytes | None,
        key_id: str | None,
        verification_keys: dict[str, bytes] | None = None,
        lsb_enabled: bool = True,
        allow_empty_fields: bool = True,
        max_output_bytes: int | None = None,
    ):
        self._key = key
        self._key_id = key_id
        self._keys = dict(verification_keys or {})
        if key is not None and key_id is not None:
            if key_id in self._keys and self._keys[key_id] != key:
                raise WatermarkError("当前密钥版本与历史核验密钥冲突。")
            self._keys[key_id] = key
        if len(self._keys) > 8 or any(
            not isinstance(kid, str)
            or not kid
            or len(kid.encode("utf-8")) > 32
            or not isinstance(value, bytes)
            or len(value) != 32
            for kid, value in self._keys.items()
        ):
            raise WatermarkError("核验密钥配置无效，最多保留 8 个不同版本。")
        self._lsb_enabled = lsb_enabled
        self._allow_empty_fields = allow_empty_fields
        values = os.environ if max_output_bytes is None else {"TRACE_MAX_OUTPUT_BYTES": str(max_output_bytes)}
        self.max_output_bytes = positive_int(values, "TRACE_MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES)

    @classmethod
    def from_environment(cls) -> TraceabilityService:
        """显式开启功能，配置无效时关闭签发而不泄露密钥内容。"""
        if (
            os.getenv("TRACEABILITY_ENABLED", "false").strip().casefold()
            not in _TRUE_VALUES
        ):
            return cls(key=None, key_id=None)
        try:
            key_value = os.getenv("ODYSSEIA_TRACE_KEY")
            key_id = os.getenv("ODYSSEIA_TRACE_KEY_ID")
            if not key_value or not key_id:
                raise WatermarkError("当前签发密钥和版本必须同时配置。")
            historical = json.loads(os.getenv("ODYSSEIA_TRACE_VERIFY_KEYS", "{}"))
            if not isinstance(historical, dict) or len(historical) > 8:
                raise WatermarkError("历史核验密钥必须是最多 8 项的 JSON 对象。")
            keys = {
                kid: parse_key(value)
                for kid, value in historical.items()
                if isinstance(value, str)
            }
            if len(keys) != len(historical):
                raise WatermarkError("历史核验密钥值必须是 Base64URL 字符串。")
            return cls(
                key=parse_key(key_value),
                key_id=key_id,
                verification_keys=keys,
                lsb_enabled=os.getenv("TRACE_LSB_ENABLED", "true").strip().casefold()
                in _TRUE_VALUES,
                allow_empty_fields=os.getenv("TRACE_MACRO_ALLOW_EMPTY_FIELDS", "true")
                .strip()
                .casefold()
                in _TRUE_VALUES,
            )
        except (ValueError, TypeError):
            logger.error("溯源密钥配置无效，动态溯源功能已安全关闭。")
            return cls(key=None, key_id=None)

    @property
    def available(self) -> bool:
        return self._key is not None and self._key_id is not None

    @property
    def key_id(self) -> str | None:
        return self._key_id

    @property
    def cache_version(self) -> str:
        """更改签发方案后隔离旧副本缓存，避免返回旧扩展水印。"""
        return (
            f"png-convert-v1:credential-only:lsb={int(self._lsb_enabled)}:"
            f"empty={int(self._allow_empty_fields)}:output={self.max_output_bytes}"
        )

    def verify(
        self,
        source: bytes,
        *,
        expected_card_id: str | None = None,
        expected_resource_id: str | None = None,
        expected_content_sha256: str | None = None,
        strict_layers: bool = False,
    ) -> dict:
        """分别提取载体并认证签发凭证，可核对外部可信上下文。"""
        if not self.available:
            raise TraceabilityUnavailableError("服务器尚未配置动态溯源密钥。")
        if len(source) > self.max_output_bytes:
            raise WatermarkError(f"核验样本超过 {self.max_output_bytes} 字节。")
        candidates = []
        layer_results = []
        if source.startswith(PNG_SIGNATURE):
            try:
                payloads = extract_lsb_all(
                    source, keys=list(self._keys.values()), repetition=3,
                    max_input_bytes=self.max_output_bytes,
                )
                for raw in payloads:
                    try:
                        candidates.append(("pixel:lsb", json.loads(raw)))
                    except (ValueError, UnicodeDecodeError):
                        candidates.append(("pixel:lsb", None))
            except ExtractionNotFound:
                pass
            except (LSBWatermarkError, LSBSkipError) as exc:
                # 不支持的像素格式不妨碍结构层核验；报告明确记录检查限制。
                layer_results.append(
                    {"source": "pixel:lsb", "status": "unavailable", "error": str(exc)}
                )
        result = verify_watermark(
            source,
            keys=self._keys,
            expected_card_id=expected_card_id,
            expected_resource_id=expected_resource_id,
            expected_content_sha256=expected_content_sha256,
            strict_layers=strict_layers,
            extra_envelopes=candidates,
            extra_layer_results=layer_results,
        )
        result["context_status"] = (
            "matched"
            if any(
                value is not None
                for value in (
                    expected_card_id,
                    expected_resource_id,
                    expected_content_sha256,
                )
            )
            else "not_provided"
        )
        return result

    @staticmethod
    def validate_character_card(filename: str, data: bytes) -> None:
        """限制输入大小并验证角色卡主体，不把任意 JSON 当作角色卡。"""
        if len(data) > MAX_SOURCE_BYTES:
            raise WatermarkError("动态溯源仅支持不超过 25 MiB 的角色卡。")
        suffix = Path(filename).suffix.lower()
        if suffix == ".png":
            documents = [document for _, _, document in card_documents(parse_png(data))]
        elif suffix == ".json":
            try:
                documents = [json.loads(data)]
            except (ValueError, UnicodeDecodeError) as exc:
                raise WatermarkError("角色卡 JSON 无效。") from exc
        else:
            raise WatermarkError("动态溯源仅支持角色卡。")
        if not documents:
            raise WatermarkError("PNG 中没有找到 tEXt/chara 或 tEXt/ccv3 角色卡数据。")
        for document in documents:
            if not isinstance(document, dict):
                raise WatermarkError("角色卡 JSON 必须是对象。")
            body = document.get("data", document)
            if not isinstance(body, dict) or not isinstance(body.get("name"), str):
                raise WatermarkError("角色卡缺少有效的 name 字段。")
            if not any(
                isinstance(body.get(field), str)
                for field in ("mes_example", "personality", "scenario")
            ):
                raise WatermarkError("角色卡缺少可支持的文本字段。")

    async def personalize(
        self,
        source: bytes,
        *,
        filename: str,
        user_id: int,
        public_thread_id: int,
        resource_id: int,
        author_id: int | None = None,
    ) -> PersonalizedCard:
        """签发新副本，保留源文件；已知像素限制允许降级交付。"""
        if not self.available:
            raise TraceabilityUnavailableError("服务器尚未配置动态溯源密钥。")
        self.validate_character_card(filename, source)
        return await asyncio.to_thread(
            self._personalize,
            source,
            filename,
            user_id,
            public_thread_id,
            resource_id,
            author_id,
        )

    def _personalize(
        self,
        source: bytes,
        filename: str,
        user_id: int,
        public_thread_id: int,
        resource_id: int,
        author_id: int | None,
    ) -> PersonalizedCard:
        """在线程内完成有界的结构与像素操作，避免阻塞事件循环。"""
        assert self._key is not None and self._key_id is not None
        suffix = Path(filename).suffix.lower()
        if suffix == ".json":
            output, _ = inject_json_watermark(
                source,
                key=self._key,
                key_id=self._key_id,
                user_id=str(user_id),
                card_id=f"discord-thread:{public_thread_id}",
                resource_id=str(resource_id),
                author_id=str(author_id) if author_id is not None else None,
                allow_empty_fields=self._allow_empty_fields,
            )
        else:
            output = self._personalize_png(
                source, user_id, public_thread_id, resource_id, author_id
            )
        if len(output) > self.max_output_bytes:
            raise WatermarkError(f"交付副本超过 {self.max_output_bytes} 字节。")
        return PersonalizedCard(output, f"{Path(filename).stem}.personalized{suffix}")

    def _ensure_no_pixel_identity(self, source: bytes) -> None:
        """保留原像素交付前检查已知旧身份，防止结构层与像素层冲突。"""
        try:
            old = verify_watermark(source, keys=self._keys)
        except WatermarkError:
            old = None
        if old and "pixel:lsb" in old["payload"].get("carriers", []):
            raise WatermarkError("源文件声明已有像素水印；无法安全降级，请使用原始源文件。")
        try:
            old_pixels = extract_lsb_all(
                source, keys=list(self._keys.values()), repetition=3,
                max_input_bytes=self.max_output_bytes,
            )
        except (ExtractionNotFound, LSBSkipError, LSBWatermarkError):
            old_pixels = []
        if old_pixels:
            raise WatermarkError("源文件包含已有像素水印；请使用原始源文件，或开启像素层后重新签发。")

    def _personalize_png(
        self, source: bytes, user_id: int, public_thread_id: int,
        resource_id: int, author_id: int | None,
    ) -> bytes:
        """使用同一签发参数生成真实载体清单，像素受限时从源图重新签发。"""
        assert self._key is not None and self._key_id is not None
        inject = partial(
            inject_watermark, key=self._key, key_id=self._key_id, user_id=str(user_id),
            card_id=f"discord-thread:{public_thread_id}", resource_id=str(resource_id),
            author_id=str(author_id) if author_id is not None else None,
            allow_empty_fields=self._allow_empty_fields,
        )
        if not self._lsb_enabled:
            self._ensure_no_pixel_identity(source)
            return inject(source)[0]
        output, _ = inject(source, expected_extra_sources=("pixel:lsb",))
        envelope = next(
            chunk.data for chunk in parse_png(output) if chunk.chunk_type == TRACE_CHUNK_TYPE
        )
        try:
            return embed_lsb(
                output, envelope, key=self._key, repetition=3,
                max_output_bytes=self.max_output_bytes,
            )
        except LSBSkipError as error:
            # 丢弃未交付的尝试，不保留错误的像素层承诺或半转换图像。
            self._ensure_no_pixel_identity(source)
            logger.warning("跳过 LSB 继续交付：resource=%s reason=%s", resource_id, error.reason)
            return inject(
                source,
                pixel_layer={"status": "skipped", "reason": error.reason, "detail": str(error)},
            )[0]
