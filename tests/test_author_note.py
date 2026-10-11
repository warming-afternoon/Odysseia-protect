"""作者备注的填写、维护、展示和无长度约束存储验收。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from sqlalchemy import select

from src.config import AUTHOR_NOTE_MAX_LENGTH
from src.dto.resource_dto import ResourceDTO
from src.enums import UploadMode
from src.models import Resource, Thread
from src.qo.resource_create import ResourceCreate
from src.qo.resource_update import ResourceUpdate
from src.qo.resource_selection_qo import ResourceSelectionQo
from src.services.delivery_service import DeliveryResult
from src.ui.download_embed_builder import DownloadEmbedBuilder
from src.ui.management_modal import ManagementModal
from src.ui.normal_upload_modal import NormalUploadModal
from src.ui.secure_upload_modal import SecureUploadModal
from tests.test_collaborators import grant, interaction, services, sessions
from tests.test_download_pagination import service


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "secure", "secure_batch"])
@pytest.mark.parametrize("note", [None, " \n ", "**使用说明**\n第二行", "备" * 400])
async def test_upload_persists_notes_for_each_resource(db_session, mode, note):
    """普通、受保护和多附件上传均规范化并保存作者备注。"""
    # 授权协作者上传，文件交付使用模拟仓库避免外部副作用。
    await grant(db_session)
    upload, _ = services()
    actor = interaction()
    actor.channel.fetch_message = AsyncMock(return_value=SimpleNamespace(
        id=99, attachments=[], content="资源",
    ))
    warehouse = MagicMock(spec=discord.Thread)
    warehouse.send = AsyncMock(side_effect=[SimpleNamespace(id=99), SimpleNamespace(id=100)])
    upload._find_or_create_warehouse_thread = AsyncMock(return_value=warehouse)
    file = MagicMock(spec=discord.Attachment)
    file.filename = "resource.zip"
    file.to_file = AsyncMock(return_value=MagicMock(spec=discord.File))
    if mode == "secure_batch":
        result = await upload.handle_secure_upload_submission_from_message(
            db_session, interaction=actor, attachments=[file, file],
            version_info="v1", author_note=note,
        )
    else:
        result = await upload.handle_upload_submission(
            db_session, interaction=actor, mode=mode, version_info="v1", author_note=note,
            file=file if mode == "secure" else None,
            message_link="https://discord.com/channels/1/10/99" if mode == "normal" else None,
        )
    assert "成功" in result
    resources = list((await db_session.scalars(select(Resource))).all())
    assert len(resources) == (2 if mode == "secure_batch" else 1)
    assert all(resource.author_note == ((note or "").strip() or None) for resource in resources)
    assert all(resource.password is None for resource in resources)
    if mode == "secure_batch":
        # 后续编辑其中一项不会改变同批其他资源的备注。
        _, manage = services()
        await manage.update_resource(db_session, resource_id=resources[0].id,
                                     interaction=actor, version_info="v1", author_note="单独编辑")
        assert resources[1].author_note == ((note or "").strip() or None)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "secure", "secure_batch"])
async def test_upload_rejects_401_characters_before_any_side_effect(db_session, mode):
    """超长备注在文件上传及数据库创建之前被拒绝。"""
    upload, _ = services()
    upload._get_or_create_thread = AsyncMock()
    upload._find_or_create_warehouse_thread = AsyncMock()
    file = MagicMock(spec=discord.Attachment)
    file.to_file = AsyncMock()
    actor = interaction(user=100)
    if mode == "secure_batch":
        result = await upload.handle_secure_upload_submission_from_message(
            db_session, interaction=actor, attachments=[file], version_info="v1", author_note="备" * 401,
        )
    else:
        result = await upload.handle_upload_submission(
            db_session, interaction=actor, mode=mode, file=file,
            version_info="v1", author_note="备" * 401,
        )
    assert "400" in result
    upload._get_or_create_thread.assert_not_awaited()
    upload._find_or_create_warehouse_thread.assert_not_awaited()
    file.to_file.assert_not_awaited()
    assert not list((await db_session.scalars(select(Resource))).all())


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [100, 200])
async def test_author_and_collaborator_manage_notes_without_rewriting_password(db_session, user_id):
    """作者和协作者可新增、修改、清空备注，并保留旧密码及描述。"""
    await grant(db_session)
    thread = Thread(public_thread_id=10, author_id=100)
    db_session.add(thread)
    await db_session.flush()
    resource = Resource(thread_id=thread.id, upload_mode=UploadMode.SECURE, filename="card.png",
                        version_info="v1", source_message_id=99, password="old-secret", description="旧描述")
    db_session.add(resource)
    await db_session.flush()
    _, manage = services()
    actor = interaction(user=user_id)
    # 400 字符边界允许保存，空白明确清空为 NULL。
    for note in ("新备注", "**说明**\n第二行", "备" * 400, "", " \n "):
        updated = await manage.update_resource(db_session, resource_id=resource.id,
                                              interaction=actor, version_info="v2", author_note=note)
        await db_session.flush()
        assert updated.author_note == (note.strip() or None)
        assert updated.password == "old-secret" and updated.description == "旧描述"
    with pytest.raises(ValueError, match="400"):
        await manage.update_resource(db_session, resource_id=resource.id,
                                     interaction=actor, version_info="invalid", author_note="备" * 401)
    assert resource.version_info == "v2" and resource.author_note is None


@pytest.mark.asyncio
async def test_note_edit_modal_preloads_and_persists_then_clears(db_session, monkeypatch):
    """管理表单回显备注并通过真实服务完成更新及清空。"""
    thread = Thread(public_thread_id=10, author_id=100)
    db_session.add(thread)
    await db_session.flush()
    resource = Resource(thread_id=thread.id, upload_mode=UploadMode.NORMAL, filename="card.png",
                        version_info="v1", source_message_id=99, author_note="原备注", password="old-secret")
    db_session.add(resource)
    await db_session.flush()
    _, manage = services()
    actor = interaction(user=100)
    view = SimpleNamespace(original_interaction=actor, refresh=AsyncMock())
    monkeypatch.setattr("src.ui.management_modal.AsyncSessionLocal", sessions(db_session))
    # 提交表单后读回数据库，确保备注不是仅在 UI 中临时保存。
    for value in ("**新说明**\n第二行", " \n "):
        modal = ManagementModal(resource, manage, view)
        assert modal.author_note_input.default == (resource.author_note or "")
        modal.version_info_input._value = "v2"
        modal.author_note_input._value = value
        await modal.on_submit(actor)
        await db_session.refresh(resource)
        assert resource.author_note == (value.strip() or None)
        assert resource.password == "old-secret"
    assert view.refresh.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "secure", "secure_batch", "manage"])
async def test_forms_have_multiline_400_character_note_and_no_password(mode):
    """用户可见表单统一限制备注长度并移除密码输入。"""
    service_mock = MagicMock()
    if mode == "manage":
        resource = Resource(version_info="v1", author_note="既有备注")
        modal = ManagementModal(resource, service_mock, MagicMock())
    elif mode == "normal":
        modal = NormalUploadModal(service_mock, None, 100)
    else:
        modal = SecureUploadModal(service_mock, [] if mode == "secure_batch" else MagicMock(), 100)
    assert modal.author_note_input.max_length == AUTHOR_NOTE_MAX_LENGTH == 400
    assert modal.author_note_input.style == discord.TextStyle.paragraph
    assert not modal.author_note_input.required
    assert not hasattr(modal, "password_input")
    assert all("密码" not in item.label for item in modal.children)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "secure", "secure_batch"])
async def test_upload_forms_forward_note_to_submission(mode, monkeypatch):
    """上传表单将用户备注传递至单文件和多附件业务入口。"""
    service_mock = MagicMock()
    service_mock.handle_upload_submission = AsyncMock(return_value="成功")
    service_mock.handle_secure_upload_submission_from_message = AsyncMock(return_value="成功")
    actor = interaction(user=100)
    monkeypatch.setattr("src.ui.normal_upload_modal.AsyncSessionLocal", sessions(MagicMock()))
    monkeypatch.setattr("src.ui.secure_upload_modal.AsyncSessionLocal", sessions(MagicMock()))
    if mode == "normal":
        modal = NormalUploadModal(service_mock, None, 100)
    else:
        modal = SecureUploadModal(service_mock, [] if mode == "secure_batch" else MagicMock(), 100)
    modal.version_info_input._value = "v1"
    modal.author_note_input._value = "**说明**\n保留换行"
    await modal.on_submit(actor)
    submission = (service_mock.handle_secure_upload_submission_from_message
                  if mode == "secure_batch" else service_mock.handle_upload_submission)
    assert submission.await_args.kwargs["author_note"] == "**说明**\n保留换行"
    assert "password" not in submission.await_args.kwargs


@pytest.mark.parametrize("mode", [UploadMode.NORMAL, UploadMode.SECURE])
@pytest.mark.parametrize("delivery_mode", ["direct", "trace_url", "trace_attachment"])
@pytest.mark.parametrize("note", [None, "", " \n ", "**说明**\n第二行", "备" * 400])
def test_download_panels_render_notes_in_order(mode, delivery_mode, note):
    """各类交付的备注均位于文件信息和下载说明之间，空白不展示。"""
    resource = ResourceDTO(id=1, filename="card.png", version_info="v1", source_message_id=99,
                           public_thread_id=10, author_note=note, upload_mode=mode,
                           trace_enabled=delivery_mode != "direct")
    url = "https://example.com/card.png"
    if delivery_mode == "direct":
        embed = DownloadEmbedBuilder.build_download_embed(resource, url)
    else:
        delivery = DeliveryResult(filename="card.png", url=url if delivery_mode == "trace_url" else None,
                                  file_data=b"card" if delivery_mode == "trace_attachment" else None)
        embed = DownloadEmbedBuilder.build_delivery_embed(resource, delivery)
    description = embed.description
    if note and note.strip():
        assert description.index("文件") < description.index("作者备注")
        assert description.index(note.strip()) < description.index("R2" if delivery_mode == "trace_attachment" else "SillyTavern")
        assert note.strip() in description
    else:
        assert "作者备注" not in description
    assert len(description) <= 4096 and len(embed) <= 6000


@pytest.mark.asyncio
async def test_storage_contract_and_database_accept_future_long_notes(db_session):
    """基础契约和 Text 列支持超过用户表单上限的备注。"""
    note = "扩展备注" * 1000
    thread = Thread(public_thread_id=300, author_id=100)
    db_session.add(thread)
    await db_session.flush()
    create = ResourceCreate(thread_id=thread.id, upload_mode=UploadMode.NORMAL, filename="card.png",
                            version_info="v1", source_message_id=99, author_note=note)
    resource = Resource(**create.model_dump())
    db_session.add(resource)
    await db_session.flush()
    dto = await service().get_selected_resource(db_session, ResourceSelectionQo(public_thread_id=300, resource_id=resource.id))
    assert dto.author_note == resource.author_note == note
    assert Resource.author_note.type.length is None
    assert ResourceUpdate(author_note=note).author_note == note
    assert ResourceUpdate().model_dump(exclude_unset=True) == {}
    assert ResourceUpdate(author_note=None).model_dump(exclude_unset=True) == {"author_note": None}
