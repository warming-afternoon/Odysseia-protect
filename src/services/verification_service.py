"""管理员批量核验 PNG、ZIP 与 7z 溯源样本。"""

from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import secrets
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import discord
import py7zr
from sqlalchemy import delete, select, update

from src.database.database import AsyncSessionLocal
from src.database.models import TraceVerificationJob
from src.services.object_store import R2ObjectStore
from src.services.traceability_service import TraceabilityService
from src.traceability.watermark import WatermarkError

logger = logging.getLogger(__name__)

MAX_ARCHIVE_ENTRIES = 1000
MAX_EXPANDED_BYTES = 3 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 25 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
REPORT_RETENTION_DAYS = 7


def _utcnow_naive() -> datetime:
    """SQLAlchemy 当前字段使用无时区 UTC 时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


class ArchiveValidationError(ValueError):
    """压缩包不满足安全限制。"""


@dataclass(frozen=True)
class VerificationSubmission:
    report_id: str
    filename: str


@dataclass(frozen=True)
class ReportArtifacts:
    markdown: bytes
    details_json: bytes
    bundle: bytes


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ArchiveValidationError(f"压缩包包含不安全路径：{name!r}")
    if len(normalized) > 512:
        raise ArchiveValidationError("压缩包成员路径超过 512 字符。")
    return normalized


def _inline_code(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").replace("`", "ˋ")
    return f"`{text}`"


def _technical_report(report: dict) -> dict:
    """生成对外技术报告，不改变内部水印协议及核验结果。"""
    rendered = copy.deepcopy(report)
    for record in rendered.get("records", []):
        payload = record.get("payload")
        if isinstance(payload, dict) and "uid" in payload:
            payload["downloader_discord_user_id"] = payload.pop("uid")
    return rendered


def _issued_at_text(value: object) -> str:
    try:
        timestamp = int(value)
        return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value)


def _render_markdown_report(report: dict) -> str:
    status_labels = {
        "matched": "命中",
        "not_found": "未找到凭证",
        "invalid": "凭证无效",
        "skipped": "已跳过",
    }
    layer_labels = {
        "complete": "水印层完整",
        "degraded": "水印层不完整",
    }
    summary = report.get("summary") or {}
    lines = [
        "# 动态溯源核验报告",
        "",
        f"- 报告编号：{_inline_code(report.get('report_id', '未知'))}",
        f"- 输入文件：{_inline_code(report.get('input_filename', '未知'))}",
        f"- 生成时间（UTC）：{_inline_code(report.get('generated_at', '未知'))}",
        "",
        "## 汇总",
        "",
        "| 项目 | 数量 |",
        "| --- | ---: |",
        f"| 已处理 PNG | {summary.get('processed', 0)} |",
        f"| 命中 | {summary.get('matched', 0)} |",
        f"| 未找到凭证 | {summary.get('not_found', 0)} |",
        f"| 凭证无效 | {summary.get('invalid', 0)} |",
        f"| 已跳过项目 | {summary.get('skipped', 0)} |",
        "",
        "## 逐项结果",
    ]

    records = report.get("records") or []
    if not records:
        lines.extend(("", "没有可报告的项目。"))
    for index, record in enumerate(records, start=1):
        status = str(record.get("status", "unknown"))
        lines.extend(
            (
                "",
                f"### {index}. {_inline_code(record.get('name', '未命名'))}",
                "",
                f"- 核验结果：**{status_labels.get(status, status)}**",
            )
        )
        payload = record.get("payload")
        if isinstance(payload, dict):
            lines.extend(
                (
                    f"- 下载者 Discord 用户 ID：{_inline_code(payload.get('uid', '未知'))}",
                    f"- 作品/帖子标识：{_inline_code(payload.get('card_id', '未知'))}",
                    f"- 资源 ID：{_inline_code(payload.get('resource_id', '未知'))}",
                    "- 凭证签发时间（UTC）："
                    f"{_inline_code(_issued_at_text(payload.get('issued_at')))}",
                    f"- 协议版本：{_inline_code(payload.get('v', '未知'))}",
                )
            )
        if "key_id" in record:
            lines.append(f"- 密钥版本：{_inline_code(record['key_id'])}")
        if "layer_status" in record:
            layer_status = str(record["layer_status"])
            lines.append(
                f"- 水印层状态：**{layer_labels.get(layer_status, layer_status)}**"
            )
        sources = record.get("sources") or []
        missing_sources = record.get("missing_sources") or []
        if sources:
            lines.append(
                "- 已发现水印层：" + "、".join(_inline_code(item) for item in sources)
            )
        if missing_sources:
            lines.append(
                "- 缺失水印层："
                + "、".join(_inline_code(item) for item in missing_sources)
            )
        if record.get("error"):
            lines.append(f"- 说明：{_inline_code(record['error'])}")
    return "\n".join(lines) + "\n"


def build_report_artifacts(report: dict) -> ReportArtifacts:
    report_id = str(report.get("report_id") or "trace-report")
    markdown = _render_markdown_report(report).encode("utf-8")
    details_json = json.dumps(
        _technical_report(report), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")
    bundle_buffer = io.BytesIO()
    with zipfile.ZipFile(bundle_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{report_id}-report.md", markdown)
        archive.writestr(f"{report_id}-details.json", details_json)
    return ReportArtifacts(
        markdown=markdown,
        details_json=details_json,
        bundle=bundle_buffer.getvalue(),
    )


def _discord_report_files(report: dict) -> list[discord.File]:
    report_id = str(report.get("report_id") or "trace-report")
    artifacts = build_report_artifacts(report)
    return [
        discord.File(
            io.BytesIO(artifacts.markdown), filename=f"{report_id}-report.md"
        ),
        discord.File(
            io.BytesIO(artifacts.details_json), filename=f"{report_id}-details.json"
        ),
    ]


class VerificationService:
    def __init__(
        self,
        traceability: TraceabilityService,
        object_store: R2ObjectStore,
    ):
        self.traceability = traceability
        self.object_store = object_store
        self._tasks: set[asyncio.Task[None]] = set()
        self._cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TraceVerificationJob)
                .where(TraceVerificationJob.status.in_(("queued", "processing")))
                .values(
                    status="failed",
                    error_message="Bot 在任务完成前重启，请重新提交样本。",
                    updated_at=_utcnow_naive(),
                )
            )
            await session.commit()
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(), name="trace-report-cleanup"
            )

    async def close(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    @staticmethod
    def new_report_id() -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return f"TR-{stamp}-{secrets.token_hex(3).upper()}"

    async def submit(
        self,
        *,
        interaction: discord.Interaction,
        attachments: list[discord.Attachment],
    ) -> list[VerificationSubmission]:
        if interaction.guild_id is None or interaction.channel_id is None:
            raise ValueError("溯源核验只能在服务器频道中使用。")

        payloads: list[tuple[VerificationSubmission, bytes]] = []
        for attachment in attachments:
            suffix = Path(attachment.filename).suffix.lower()
            if suffix not in {".png", ".zip", ".7z"}:
                raise ValueError(
                    f"不支持 `{attachment.filename}`；只接受 PNG、ZIP 或 7z。"
                )
            payloads.append(
                (
                    VerificationSubmission(
                        report_id=self.new_report_id(),
                        filename=attachment.filename,
                    ),
                    await attachment.read(),
                )
            )

        expires_at = _utcnow_naive() + timedelta(days=REPORT_RETENTION_DAYS)
        async with AsyncSessionLocal() as session:
            for submission, _ in payloads:
                session.add(
                    TraceVerificationJob(
                        report_id=submission.report_id,
                        requester_id=interaction.user.id,
                        guild_id=interaction.guild_id,
                        channel_id=interaction.channel_id,
                        input_filename=submission.filename,
                        status="queued",
                        expires_at=expires_at,
                    )
                )
            await session.commit()

        for submission, data in payloads:
            task = asyncio.create_task(
                self._process_and_notify(interaction, submission, data),
                name=f"trace-verify-{submission.report_id}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return [submission for submission, _ in payloads]

    async def _process_and_notify(
        self,
        interaction: discord.Interaction,
        submission: VerificationSubmission,
        data: bytes,
    ) -> None:
        await self._set_status(submission.report_id, "processing")
        try:
            report = await asyncio.to_thread(
                self.verify_attachment,
                filename=submission.filename,
                data=data,
                report_id=submission.report_id,
            )
            artifacts = build_report_artifacts(report)
            report_key = None
            if self.object_store.available:
                report_key = (
                    f"reports/{time.strftime('%Y/%m/%d')}/"
                    f"{submission.report_id}.zip"
                )
                await self.object_store.put_bytes(
                    report_key,
                    artifacts.bundle,
                    content_type="application/zip",
                    metadata={"report-id": submission.report_id},
                )
            await self._finish_job(
                submission.report_id,
                summary_json=json.dumps(report, ensure_ascii=False),
                report_object_key=report_key,
            )
            summary = report["summary"]
            message = (
                f"✅ 核验完成 `{submission.report_id}` · `{submission.filename}`\n"
                f"处理 {summary['processed']} 张，命中 {summary['matched']} 张，"
                f"无凭证 {summary['not_found']} 张，无效 {summary['invalid']} 张，"
                f"跳过 {summary['skipped']} 项。"
            )
            try:
                await interaction.followup.send(
                    message,
                    files=_discord_report_files(report),
                    ephemeral=True,
                )
            except discord.HTTPException:
                logger.info(
                    "核验任务 %s 完成，但原 interaction 已无法跟进。",
                    submission.report_id,
                )
        except Exception as exc:
            logger.exception("核验任务失败：%s", submission.report_id)
            await self._fail_job(submission.report_id, str(exc))
            try:
                await interaction.followup.send(
                    f"❌ 核验失败 `{submission.report_id}`：{exc}",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

    def verify_attachment(
        self,
        *,
        filename: str,
        data: bytes,
        report_id: str,
    ) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix == ".png":
            records = [self._verify_png(filename, data)]
        elif suffix == ".zip":
            records = self._verify_zip(data)
        elif suffix == ".7z":
            records = self._verify_7z(data)
        else:
            raise ValueError("不支持的附件格式。")
        summary = {
            "processed": sum(item["status"] != "skipped" for item in records),
            "matched": sum(item["status"] == "matched" for item in records),
            "not_found": sum(item["status"] == "not_found" for item in records),
            "invalid": sum(item["status"] == "invalid" for item in records),
            "skipped": sum(item["status"] == "skipped" for item in records),
        }
        return {
            "report_id": report_id,
            "input_filename": filename,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "retention_days": REPORT_RETENTION_DAYS,
            "summary": summary,
            "records": records,
        }

    def _verify_png(self, name: str, data: bytes) -> dict:
        try:
            result = self.traceability.verify(data)
            return {
                "name": name,
                "status": "matched",
                "payload": result["payload"],
                "key_id": result["key_id"],
                "sources": result["sources"],
                "missing_sources": result["missing_sources"],
                "layer_status": result["layer_status"],
            }
        except WatermarkError as exc:
            text = str(exc)
            status = "not_found" if "没有找到溯源凭证" in text else "invalid"
            return {"name": name, "status": status, "error": text}

    def _verify_zip(self, data: bytes) -> list[dict]:
        records: list[dict] = []
        total = 0
        seen: set[str] = set()
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError) as exc:
            raise ArchiveValidationError("ZIP 文件损坏或格式无效。") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise ArchiveValidationError("ZIP 成员数量超过 1000。")
            for info in infos:
                name = _safe_member_name(info.filename)
                if name in seen:
                    raise ArchiveValidationError(f"ZIP 包含重复路径：{name}")
                seen.add(name)
                if info.flag_bits & 0x1:
                    raise ArchiveValidationError("不接受带密码或加密的 ZIP。")
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ArchiveValidationError("ZIP 不允许符号链接。")
                if info.is_dir():
                    continue
                total += info.file_size
                self._validate_sizes(name, info.file_size, info.compress_size, total)
                if Path(name).suffix.lower() != ".png":
                    records.append({"name": name, "status": "skipped"})
                    continue
                with archive.open(info) as member:
                    payload = member.read(MAX_MEMBER_BYTES + 1)
                if len(payload) > MAX_MEMBER_BYTES:
                    raise ArchiveValidationError(f"成员超过 25 MiB：{name}")
                records.append(self._verify_png(name, payload))
        return records

    def _verify_7z(self, data: bytes) -> list[dict]:
        records: list[dict] = []
        with tempfile.TemporaryDirectory(prefix="odysseia-trace-") as temp_dir:
            try:
                with py7zr.SevenZipFile(io.BytesIO(data), mode="r") as archive:
                    if archive.needs_password():
                        raise ArchiveValidationError("不接受带密码或加密的 7z。")
                    infos = archive.list()
                    if len(infos) > MAX_ARCHIVE_ENTRIES:
                        raise ArchiveValidationError("7z 成员数量超过 1000。")
                    targets: list[str] = []
                    total = 0
                    seen: set[str] = set()
                    for info in infos:
                        name = _safe_member_name(info.filename)
                        if name in seen:
                            raise ArchiveValidationError(f"7z 包含重复路径：{name}")
                        seen.add(name)
                        if getattr(info, "is_directory", False):
                            continue
                        if getattr(info, "is_symlink", False):
                            raise ArchiveValidationError("7z 不允许符号链接。")
                        size = int(getattr(info, "uncompressed", 0) or 0)
                        compressed = int(getattr(info, "compressed", 0) or 0)
                        total += size
                        self._validate_sizes(name, size, compressed, total)
                        if Path(name).suffix.lower() == ".png":
                            targets.append(info.filename)
                        else:
                            records.append({"name": name, "status": "skipped"})
                    if targets:
                        archive.extract(path=temp_dir, targets=targets)
            except ArchiveValidationError:
                raise
            except (py7zr.Bad7zFile, OSError) as exc:
                raise ArchiveValidationError("7z 文件损坏或格式无效。") from exc

            root = Path(temp_dir).resolve()
            for target in targets:
                candidate = (root / target).resolve()
                if root not in candidate.parents:
                    raise ArchiveValidationError("7z 解压路径逃逸。")
                payload = candidate.read_bytes()
                if len(payload) > MAX_MEMBER_BYTES:
                    raise ArchiveValidationError(f"成员超过 25 MiB：{target}")
                records.append(self._verify_png(target, payload))
        return records

    @staticmethod
    def _validate_sizes(name: str, size: int, compressed: int, total: int) -> None:
        if size > MAX_MEMBER_BYTES:
            raise ArchiveValidationError(f"成员超过 25 MiB：{name}")
        if total > MAX_EXPANDED_BYTES:
            raise ArchiveValidationError("压缩包解压总量超过 3 GiB。")
        if size and compressed == 0:
            raise ArchiveValidationError(f"成员压缩比异常：{name}")
        if compressed and size / compressed > MAX_COMPRESSION_RATIO:
            raise ArchiveValidationError(f"成员压缩比超过 100:1：{name}")

    async def _set_status(self, report_id: str, status: str) -> None:
        async with AsyncSessionLocal() as session:
            job = await session.get(TraceVerificationJob, report_id)
            if job:
                job.status = status
                job.updated_at = _utcnow_naive()
                await session.commit()

    async def _finish_job(
        self,
        report_id: str,
        *,
        summary_json: str,
        report_object_key: str | None,
    ) -> None:
        async with AsyncSessionLocal() as session:
            job = await session.get(TraceVerificationJob, report_id)
            if job:
                job.status = "completed"
                job.summary_json = summary_json
                job.report_object_key = report_object_key
                job.updated_at = _utcnow_naive()
                await session.commit()

    async def _fail_job(self, report_id: str, error: str) -> None:
        async with AsyncSessionLocal() as session:
            job = await session.get(TraceVerificationJob, report_id)
            if job:
                job.status = "failed"
                job.error_message = error[:4000]
                job.updated_at = _utcnow_naive()
                await session.commit()

    async def get_job(self, report_id: str) -> TraceVerificationJob | None:
        async with AsyncSessionLocal() as session:
            statement = select(TraceVerificationJob).where(
                TraceVerificationJob.report_id == report_id.upper()
            )
            return (await session.execute(statement)).scalar_one_or_none()

    async def report_response(
        self, job: TraceVerificationJob
    ) -> tuple[str, list[discord.File]]:
        if job.status == "failed":
            return f"❌ `{job.report_id}` 失败：{job.error_message}", []
        if job.status != "completed":
            labels = {"queued": "排队中", "processing": "处理中"}
            return f"⏳ `{job.report_id}` 当前状态：{labels.get(job.status, job.status)}", []
        if job.report_object_key and self.object_store.available:
            url = await self.object_store.presign_get(
                job.report_object_key, expires_in=900
            )
            legacy_note = (
                "（旧版 JSON 报告）"
                if job.report_object_key.endswith(".json")
                else "（ZIP 报告包）"
            )
            return (
                f"✅ `{job.report_id}` 已完成{legacy_note}。"
                f"链接 15 分钟内有效：\n{url}",
                [],
            )
        try:
            report = json.loads(job.summary_json or "{}")
        except json.JSONDecodeError:
            encoded = (job.summary_json or "{}").encode("utf-8")
            return (
                f"✅ `{job.report_id}` 已完成，但只能提供旧版原始报告。",
                [discord.File(io.BytesIO(encoded), filename=f"{job.report_id}.json")],
            )
        return f"✅ `{job.report_id}` 已完成。", _discord_report_files(report)

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        delete(TraceVerificationJob).where(
                            TraceVerificationJob.expires_at <= _utcnow_naive()
                        )
                    )
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("清理过期核验任务元数据失败。")
            await asyncio.sleep(3600)
