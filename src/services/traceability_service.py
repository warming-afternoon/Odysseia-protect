"""生成不落身份账本的个性化角色卡副本。"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from src.traceability.watermark import (
    WatermarkError,
    card_documents,
    inject_watermark,
    parse_key,
    parse_png,
    verify_watermark,
)

logger = logging.getLogger(__name__)


class TraceabilityUnavailableError(RuntimeError):
    """服务器未正确配置溯源能力。"""


@dataclass(frozen=True)
class PersonalizedCard:
    data: bytes
    filename: str


class TraceabilityService:
    """只负责角色卡校验与双层结构水印生成。"""

    def __init__(self, *, key: bytes | None, key_id: str | None):
        self._key = key
        self._key_id = key_id

    @classmethod
    def from_environment(cls) -> TraceabilityService:
        enabled = os.getenv("TRACEABILITY_ENABLED", "false").strip().casefold()
        if enabled not in {"1", "true", "yes", "on", "开启", "是"}:
            logger.info("TRACEABILITY_ENABLED 未开启，动态溯源功能保持关闭。")
            return cls(key=None, key_id=None)
        key_value = os.getenv("ODYSSEIA_TRACE_KEY")
        key_id = os.getenv("ODYSSEIA_TRACE_KEY_ID")
        if not key_value and not key_id:
            logger.info("未配置溯源密钥，动态溯源功能保持关闭。")
            return cls(key=None, key_id=None)
        if not key_value or not key_id:
            logger.error(
                "ODYSSEIA_TRACE_KEY 与 ODYSSEIA_TRACE_KEY_ID 必须同时配置；"
                "动态溯源功能已安全关闭。"
            )
            return cls(key=None, key_id=None)
        if len(key_id.encode("utf-8")) > 32:
            logger.error(
                "ODYSSEIA_TRACE_KEY_ID 超过 32 个 UTF-8 字节；动态溯源功能已安全关闭。"
            )
            return cls(key=None, key_id=None)
        try:
            key = parse_key(key_value)
        except WatermarkError as exc:
            logger.error("ODYSSEIA_TRACE_KEY 无效，动态溯源功能已安全关闭: %s", exc)
            return cls(key=None, key_id=None)
        return cls(key=key, key_id=key_id)

    @property
    def available(self) -> bool:
        return self._key is not None and self._key_id is not None

    @property
    def key_id(self) -> str | None:
        return self._key_id

    def verify(self, source: bytes) -> dict:
        """解密并验证 PNG 中的溯源凭证。"""
        if not self.available:
            raise TraceabilityUnavailableError("服务器尚未配置动态溯源密钥。")
        assert self._key is not None
        assert self._key_id is not None
        return verify_watermark(source, keys={self._key_id: self._key})

    @staticmethod
    def validate_character_card(filename: str, data: bytes) -> None:
        if Path(filename).suffix.lower() != ".png":
            raise WatermarkError("首期溯源仅支持 PNG 角色卡。")
        if not card_documents(parse_png(data)):
            raise WatermarkError(
                "PNG 中没有找到 tEXt/chara 或 tEXt/ccv3 角色卡数据。"
            )

    async def personalize(
        self,
        source: bytes,
        *,
        filename: str,
        user_id: int,
        public_thread_id: int,
        resource_id: int,
    ) -> PersonalizedCard:
        if not self.available:
            raise TraceabilityUnavailableError("服务器尚未配置动态溯源密钥。")

        self.validate_character_card(filename, source)
        assert self._key is not None
        assert self._key_id is not None
        output, _ = await asyncio.to_thread(
            inject_watermark,
            source,
            key=self._key,
            key_id=self._key_id,
            user_id=str(user_id),
            card_id=f"discord-thread:{public_thread_id}",
            resource_id=str(resource_id),
        )
        source_path = Path(filename)
        output_name = f"{source_path.stem}.personalized.png"
        return PersonalizedCard(data=output, filename=output_name)
