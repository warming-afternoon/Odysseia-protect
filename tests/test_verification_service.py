import io
import json
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import py7zr
import pytest

from src.cogs.trace_admin_cog import TraceVerificationModal, is_trace_admin
from src.services.object_store import R2Config, R2ObjectStore
from src.services.traceability_service import TraceabilityService
from src.services.verification_service import (
    ArchiveValidationError,
    VerificationService,
    build_report_artifacts,
)

CARD_PATH = Path(__file__).parents[1] / "Watermark" / "default_Seraphina.png"


@pytest.fixture
def verifier() -> VerificationService:
    traceability = TraceabilityService(key=bytes(range(32)), key_id="test-v1")
    store = R2ObjectStore(R2Config(None, None, None, None))
    return VerificationService(traceability, store)


@pytest.mark.asyncio
async def test_zip_and_7z_each_find_personalized_png(verifier):
    personalized = await verifier.traceability.personalize(
        CARD_PATH.read_bytes(),
        filename="card.png",
        user_id=123,
        public_thread_id=456,
        resource_id=789,
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cards/card.png", personalized.data)
        archive.writestr("README.txt", "ignored")
    zip_report = verifier.verify_attachment(
        filename="samples.zip", data=zip_buffer.getvalue(), report_id="TR-ZIP"
    )

    temp_root = Path(__file__).parents[1] / "temp"
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
        png_path = Path(temp_dir) / "card.png"
        png_path.write_bytes(personalized.data)
        seven_path = Path(temp_dir) / "samples.7z"
        with py7zr.SevenZipFile(seven_path, "w") as archive:
            archive.write(png_path, "cards/card.png")
        seven_report = verifier.verify_attachment(
            filename="samples.7z", data=seven_path.read_bytes(), report_id="TR-7Z"
        )

    assert zip_report["summary"] == {
        "processed": 1,
        "matched": 1,
        "not_found": 0,
        "invalid": 0,
        "skipped": 1,
    }
    assert seven_report["summary"]["matched"] == 1
    assert seven_report["records"][0]["payload"]["uid"] == "123"


def test_zip_rejects_path_traversal(verifier):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../card.png", b"bad")

    with pytest.raises(ArchiveValidationError, match="不安全路径"):
        verifier.verify_attachment(
            filename="bad.zip", data=payload.getvalue(), report_id="TR-BAD"
        )


def test_verification_command_uses_direct_file_upload_modal(monkeypatch):
    modal = TraceVerificationModal(MagicMock())
    assert len(modal.children) == 1
    assert isinstance(modal.children[0], discord.ui.Label)
    assert isinstance(modal.file_upload, discord.ui.FileUpload)
    assert modal.file_upload.min_values == 1
    assert modal.file_upload.max_values == 10

    monkeypatch.setenv("TRACE_ADMIN_USER_IDS", "123,456")
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.user.id = 123
    assert is_trace_admin(interaction) is True


def _sample_report() -> dict:
    return {
        "report_id": "TR-REPORT",
        "input_filename": "samples.zip",
        "generated_at": "2026-08-31T08:10:35+00:00",
        "retention_days": 7,
        "summary": {
            "processed": 3,
            "matched": 1,
            "not_found": 1,
            "invalid": 1,
            "skipped": 1,
        },
        "records": [
            {
                "name": "matched.png",
                "status": "matched",
                "payload": {
                    "uid": "954037609313747036",
                    "card_id": "discord-thread:123",
                    "resource_id": "13",
                    "delivery_id": "delivery",
                    "issued_at": 1788163438,
                    "v": 1,
                },
                "key_id": "v1",
                "sources": ["json:ccv3", "png:trAc"],
                "missing_sources": ["json:chara"],
                "layer_status": "degraded",
            },
            {
                "name": "missing.png",
                "status": "not_found",
                "error": "文件中没有找到溯源凭证。",
            },
            {
                "name": "invalid.png",
                "status": "invalid",
                "error": "凭证无效。",
            },
            {"name": "README.md", "status": "skipped"},
        ],
    }


def test_report_artifacts_are_chinese_and_rename_downloader_id():
    report = _sample_report()
    artifacts = build_report_artifacts(report)

    markdown = artifacts.markdown.decode("utf-8")
    assert "下载者 Discord 用户 ID" in markdown
    assert "954037609313747036" in markdown
    assert "未找到凭证" in markdown
    assert "凭证无效" in markdown
    assert "已跳过" in markdown
    assert "水印层不完整" in markdown
    assert "- 已发现水印层：`json:ccv3`、`png:trAc`" in markdown
    assert "- 缺失水印层：`json:chara`" in markdown

    details = json.loads(artifacts.details_json)
    payload = details["records"][0]["payload"]
    assert payload["downloader_discord_user_id"] == "954037609313747036"
    assert "uid" not in payload
    assert report["records"][0]["payload"]["uid"] == "954037609313747036"


def test_report_zip_contains_markdown_and_details_json():
    artifacts = build_report_artifacts(_sample_report())

    with zipfile.ZipFile(io.BytesIO(artifacts.bundle)) as archive:
        assert set(archive.namelist()) == {
            "TR-REPORT-report.md",
            "TR-REPORT-details.json",
        }
        assert "下载者 Discord 用户 ID" in archive.read("TR-REPORT-report.md").decode(
            "utf-8"
        )
        details = json.loads(archive.read("TR-REPORT-details.json"))
        assert (
            details["records"][0]["payload"]["downloader_discord_user_id"]
            == "954037609313747036"
        )


@pytest.mark.asyncio
async def test_processing_uploads_zip_and_sends_two_report_files():
    traceability = MagicMock()
    store = MagicMock()
    store.available = True
    store.put_bytes = AsyncMock()
    service = VerificationService(traceability, store)
    report = _sample_report()
    service.verify_attachment = MagicMock(return_value=report)
    service._set_status = AsyncMock()
    service._finish_job = AsyncMock()
    interaction = MagicMock()
    interaction.followup.send = AsyncMock()
    submission = SimpleNamespace(report_id="TR-REPORT", filename="samples.zip")

    await service._process_and_notify(interaction, submission, b"archive")

    put_args = store.put_bytes.await_args
    assert put_args.args[0].endswith("/TR-REPORT.zip")
    assert put_args.kwargs["content_type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(put_args.args[1])) as archive:
        assert len(archive.namelist()) == 2
    finish_args = service._finish_job.await_args.kwargs
    assert json.loads(finish_args["summary_json"])["records"][0]["payload"]["uid"]
    sent_files = interaction.followup.send.await_args.kwargs["files"]
    assert [item.filename for item in sent_files] == [
        "TR-REPORT-report.md",
        "TR-REPORT-details.json",
    ]


@pytest.mark.asyncio
async def test_report_response_falls_back_to_two_generated_files(verifier):
    job = SimpleNamespace(
        report_id="TR-REPORT",
        status="completed",
        error_message=None,
        report_object_key=None,
        summary_json=json.dumps(_sample_report(), ensure_ascii=False),
    )

    content, files = await verifier.report_response(job)

    assert "已完成" in content
    assert [item.filename for item in files] == [
        "TR-REPORT-report.md",
        "TR-REPORT-details.json",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("object_key", "label"),
    [
        ("reports/2026/08/31/TR-REPORT.zip", "ZIP 报告包"),
        ("reports/2026/08/31/TR-REPORT.json", "旧版 JSON 报告"),
    ],
)
async def test_report_response_preserves_r2_and_legacy_links(object_key, label):
    store = MagicMock()
    store.available = True
    store.presign_get = AsyncMock(return_value="https://example.test/report")
    verifier = VerificationService(MagicMock(), store)
    job = SimpleNamespace(
        report_id="TR-REPORT",
        status="completed",
        error_message=None,
        report_object_key=object_key,
        summary_json=json.dumps(_sample_report()),
    )

    content, files = await verifier.report_response(job)

    assert label in content
    assert "https://example.test/report" in content
    assert files == []


@pytest.mark.asyncio
async def test_json_direct_and_archives_are_verified(verifier, tmp_path):
    source = json.dumps(
        {"name": "test", "personality": "Calm", "scenario": "", "mes_example": ""}
    ).encode()
    marked = await verifier.traceability.personalize(
        source, filename="card.json", user_id=1, public_thread_id=2, resource_id=3
    )
    direct = verifier.verify_attachment(
        filename="card.json", data=marked.data, report_id="TR-JSON"
    )
    assert direct["summary"]["matched"] == 1
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as archive:
        archive.writestr("card.json", marked.data)
    assert (
        verifier.verify_attachment(
            filename="cards.zip", data=zipped.getvalue(), report_id="TR-ZIP-JSON"
        )["summary"]["matched"]
        == 1
    )
    source_path = tmp_path / "card.json"
    source_path.write_bytes(marked.data)
    archive_path = tmp_path / "cards.7z"
    with py7zr.SevenZipFile(archive_path, "w") as archive:
        archive.write(source_path, "card.json")
    assert (
        verifier.verify_attachment(
            filename="cards.7z", data=archive_path.read_bytes(), report_id="TR-7Z-JSON"
        )["summary"]["matched"]
        == 1
    )


@pytest.mark.asyncio
async def test_edited_card_is_reported_as_credential_match(verifier):
    source = json.dumps(
        {"name": "test", "personality": "Calm", "scenario": "", "mes_example": ""}
    ).encode()
    marked = await verifier.traceability.personalize(
        source, filename="card.json", user_id=1, public_thread_id=2, resource_id=3
    )
    edited = json.loads(marked.data)
    edited["name"] = "A different character"
    report = verifier.verify_attachment(
        filename="card.json", data=json.dumps(edited).encode(), report_id="TR-MISMATCH"
    )
    assert report["summary"]["matched"] == 1
    assert "content_mismatch" not in report["summary"]
    assert report["records"][0]["status"] == "matched"
    rendered = build_report_artifacts(report).markdown.decode()
    assert "未检查卡片内容，仅认证签发凭证" in rendered
    assert "凭证命中" in rendered
    assert "内容不匹配" not in rendered


@pytest.mark.parametrize("digest_version", [1, 2])
@pytest.mark.parametrize("carrier", ["macro", "extension"])
def test_old_credentials_with_changed_content_still_count_as_matches(
    verifier, digest_version, carrier
):
    """历史两版摘要及正式服旧扩展在正文修改后仍能核验签发信息。"""
    from src.traceability.watermark import (
        _b64encode, canonical_json, card_content_sha256, create_envelope,
    )

    original = {"name": "original", "personality": "Calm"}
    payload = {
        "v": 1, "uid": "123", "resource_id": "789", "card_id": "discord-thread:456",
        "author_id": "987", "issued_at": 100,
        "content_sha256": [card_content_sha256(original, version=digest_version)],
    }
    if digest_version == 2:
        payload["content_digest_version"] = 2
    envelope = create_envelope(payload, bytes(range(32)), "test-v1")
    edited = {"name": "edited", "personality": "Calm "}
    if carrier == "macro":
        edited["personality"] += "{{//0123456789abcdef:" + _b64encode(canonical_json(envelope)) + "}}"
    else:
        edited["extensions"] = {"odysseia_trace": envelope}
    report = verifier.verify_attachment(
        filename="old.json", data=canonical_json(edited), report_id="TR-OLD"
    )
    assert report["summary"]["matched"] == 1
    assert "content_mismatch" not in report["summary"]
    assert report["records"][0]["payload"] == payload
    assert report["records"][0]["content_status"] == "not_checked"
    rendered = build_report_artifacts(report).markdown.decode()
    assert "作者 Discord 用户 ID：`987`" in rendered
    assert "未检查卡片内容，仅认证签发凭证" in rendered
