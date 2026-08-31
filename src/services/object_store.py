"""私有 R2 对象存储的异步封装。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aioboto3


@dataclass(frozen=True)
class R2Config:
    endpoint_url: str | None
    access_key_id: str | None
    secret_access_key: str | None
    bucket_name: str | None
    region_name: str = "auto"

    @classmethod
    def from_environment(cls) -> R2Config:
        return cls(
            endpoint_url=os.getenv("R2_ENDPOINT_URL"),
            access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
            secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
            bucket_name=os.getenv("R2_BUCKET_NAME"),
            region_name=os.getenv("R2_REGION_NAME", "auto"),
        )

    @property
    def available(self) -> bool:
        return all(
            (
                self.endpoint_url,
                self.access_key_id,
                self.secret_access_key,
                self.bucket_name,
            )
        )


class R2ObjectStore:
    """只暴露动态交付所需的最小 S3 操作集合。"""

    def __init__(self, config: R2Config | None = None):
        self.config = config or R2Config.from_environment()
        self._session = aioboto3.Session()

    @property
    def available(self) -> bool:
        return self.config.available

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[object]:
        if not self.available:
            raise RuntimeError("R2 动态交付配置不完整。")
        async with self._session.client(
            "s3",
            endpoint_url=self.config.endpoint_url,
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key,
            region_name=self.config.region_name,
        ) as client:
            yield client

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        async with self._client() as client:
            await client.put_object(
                Bucket=self.config.bucket_name,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata=metadata or {},
            )

    async def presign_get(self, key: str, *, expires_in: int) -> str:
        async with self._client() as client:
            return await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.config.bucket_name, "Key": key},
                ExpiresIn=expires_in,
            )

    async def delete_many(self, keys: Iterable[str]) -> None:
        objects = [{"Key": key} for key in keys]
        if not objects:
            return
        if len(objects) > 1000:
            raise ValueError("R2 DeleteObjects 每批最多允许 1000 个对象。")
        async with self._client() as client:
            response = await client.delete_objects(
                Bucket=self.config.bucket_name,
                Delete={"Objects": objects, "Quiet": True},
            )
        errors = response.get("Errors") or []
        if errors:
            details = ", ".join(
                f"{item.get('Key')}: {item.get('Code')}" for item in errors[:5]
            )
            raise RuntimeError(f"R2 批量删除部分失败：{details}")
