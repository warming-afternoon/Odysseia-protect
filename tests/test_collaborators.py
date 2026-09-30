"""协作者权限矩阵、跨服授权、提交时撤销及命令分页。"""

import io
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.cogs.collaborator_cog import CollaboratorCog
from src.models import AuthorCollaborator, Resource, Thread, User
from src.enums import UploadMode
from src.database.repositories.collaborator import CollaboratorRepository
from src.database.repositories.resource import ResourceRepository
from src.database.repositories.thread import ThreadRepository
from src.database.repositories.user import UserRepository
from src.services.collaborator_service import CollaboratorService
from src.services.management_service import ManagementService
from src.services.upload_service import UploadService
from src.ui.management_ui import ManagementModal, ReplaceSourceModal
from src.utils.auth import (
    can_edit_collaborators, is_thread_author, parse_user_id,
    require_thread_manager, resolve_thread_author,
)


@pytest_asyncio.fixture
async def db_session(async_engine):
    """提供允许真实提交和回滚的测试数据库会话。"""
    # 使用真实提交和回滚，模拟多个独立界面请求之间的授权变更。
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        yield session


def interaction(user=200, owner=100, channel_id=10, guild_id=1):
    """构造具有指定操作人、帖主和服务器信息的模拟交互。"""
    # 准备操作人和原生帖主信息，避免权限测试依赖真实 Discord 请求。
    result = MagicMock()
    result.user.id = user
    result.user.roles = []
    result.channel = MagicMock(spec=discord.Thread)
    result.channel.id = channel_id
    result.channel.owner_id = owner
    result.channel.name = "测试帖子"
    result.channel.parent = MagicMock(spec=discord.ForumChannel)
    result.guild.id = guild_id
    # 配置异步响应接口，使错误提示、表单与分页回调可以直接断言。
    result.response.is_done.return_value = False
    result.response.send_message = AsyncMock()
    async def defer(**kwargs):
        """模拟延迟响应后交互已被确认的状态。"""
        result.response.is_done.return_value = True
    result.response.defer = AsyncMock(side_effect=defer)
    result.response.edit_message = AsyncMock()
    result.response.send_modal = AsyncMock()
    result.followup.send = AsyncMock()
    result.edit_original_response = AsyncMock()
    return result


def sessions(session):
    """将已有测试会话包装为可替换的异步会话工厂。"""
    @asynccontextmanager
    async def factory():
        """在异步上下文中复用当前测试会话。"""
        yield session
    return factory


def services():
    """构造共享模拟机器人和仓储的上传及管理服务。"""
    bot = MagicMock()
    repos = (ResourceRepository(), ThreadRepository(), UserRepository())
    return UploadService(bot, *repos), ManagementService(bot, *repos)


async def grant(session, author=100, user=200):
    """创建并提交指定原作者与协作者之间的授权关系。"""
    await CollaboratorRepository().add(session, author_id=author, user_id=user, granted_by=author)
    await session.commit()


@pytest.mark.parametrize("value", ["123", "<@123>", "<@!123>", " 123 "])
def test_parse_ids(value):
    """验证数字账号与用户提及均能解析为账号标识。"""
    # 不同输入形式应指向同一个用户账号。
    assert parse_user_id(value) == 123


@pytest.mark.parametrize("value", ["", "abc", "<@&123>", "-1", "0", "１２３", "9223372036854775808"])
def test_invalid_ids(value):
    """验证非法格式及超出存储范围的账号被拒绝。"""
    # 无效输入必须在进入授权逻辑前抛出明确的参数错误。
    with pytest.raises(ValueError):
        parse_user_id(value)


@pytest.mark.asyncio
async def test_cross_guild_future_threads_and_no_transitive_grants(db_session, monkeypatch):
    """验证跨服、多对多和未来帖子授权生效且授权不传递。"""
    # 建立同一协作者服务多个作者以及协作者再授权他人的关系。
    await grant(db_session)
    await grant(db_session, author=200, user=300)
    await grant(db_session, author=400, user=200)
    # 不预建帖子记录，直接验证授权覆盖不同服务器的未来帖子。
    for guild in (1, 2):
        assert await require_thread_manager(db_session, interaction=interaction(guild_id=guild)) == 100
        assert await require_thread_manager(db_session, interaction=interaction(owner=400, guild_id=guild)) == 400
    assert await require_thread_manager(db_session, interaction=interaction(user=100)) == 100
    # 间接获得授权的账号与陌生用户都不能维护原作者资源。
    for user in (300, 999):
        with pytest.raises(PermissionError):
            await require_thread_manager(db_session, interaction=interaction(user=user))
    # 管理组可管理全局名单，但不会自动获得资源维护权限。
    monkeypatch.setenv("TRACE_ADMIN_USER_IDS", "999")
    assert can_edit_collaborators(interaction(user=999), 100)
    with pytest.raises(PermissionError):
        await require_thread_manager(db_session, interaction=interaction(user=999))
    assert not can_edit_collaborators(interaction(), 100)


@pytest.mark.asyncio
async def test_author_resolution_native_then_database_and_unknown_denied(db_session):
    """验证优先使用原生帖主信息、数据库回退及未知作者拒绝。"""
    # 模拟原生帖主信息缺失且没有帖子记录的情况。
    channel = interaction(owner=None).channel
    assert await resolve_thread_author(db_session, public_thread_id=10, channel=channel) is None
    assert not await is_thread_author(db_session, public_thread_id=10, user_id=200, channel=channel)
    with pytest.raises(PermissionError, match="无法确认"):
        await require_thread_manager(db_session, interaction=interaction(owner=None))
    # 已有帖子记录可作为回退，但原生帖主信息存在时优先采用。
    db_session.add(Thread(public_thread_id=10, author_id=100))
    await db_session.commit()
    assert await resolve_thread_author(db_session, public_thread_id=10, channel=channel) == 100
    assert await resolve_thread_author(db_session, public_thread_id=10, channel=interaction(owner=400).channel) == 400
    # 回退作者的协作者可操作，但不能沿用授权维护其他作者的帖子。
    await grant(db_session)
    assert await require_thread_manager(db_session, interaction=interaction(owner=None)) == 100
    with pytest.raises(PermissionError):
        await require_thread_manager(db_session, interaction=interaction(owner=400))


@pytest.mark.asyncio
async def test_author_and_admin_can_change_but_collaborator_cannot(db_session, monkeypatch, caplog):
    """验证作者与管理组能修改授权而协作者不能管理名单。"""
    # 由原作者添加授权，并检查成功提示和完整操作日志。
    service = CollaboratorService()
    owner = interaction(user=100)
    with caplog.at_level("INFO"):
        assert "所有服务器" in await service.change(db_session, interaction=owner,
                                                     author_id=100, user_id=200, adding=True)
    assert "operator=100 author=100 collaborator=200 guild=1" in caplog.text
    # 重复授权不新增记录，也不要求双方已有用户档案。
    assert "已经" in await service.change(db_session, interaction=owner,
                                         author_id=100, user_id=200, adding=True)
    assert (await db_session.execute(select(func.count()).select_from(User))).scalar_one() == 0
    # 自我授权和协作者修改他人名单都必须被拒绝。
    with pytest.raises(ValueError):
        await service.change(db_session, interaction=owner, author_id=100, user_id=100, adding=True)
    for adding in (True, False):
        with pytest.raises(PermissionError):
            await service.change(db_session, interaction=interaction(), author_id=100,
                                 user_id=300, adding=adding)
    # 管理组角色可为失联账号授权，并保留实际操作人和创建时间。
    admin = interaction(user=999)
    monkeypatch.setenv("TRACE_ADMIN_ROLE_IDS", "777")
    admin.user.roles = [SimpleNamespace(id=777)]
    assert "已添加" in await service.change(db_session, interaction=admin,
                                            author_id=555, user_id=666, adding=True)
    record = (await db_session.execute(select(AuthorCollaborator).where(
        AuthorCollaborator.author_id == 555,
    ))).scalar_one()
    assert record.granted_by == 999 and record.created_at is not None
    # 不依赖服务器成员记录即可移除授权，重复移除返回不存在提示。
    assert "已移除" in await service.change(db_session, interaction=admin,
                                            author_id=555, user_id=666, adding=False)
    assert "不在" in await service.change(db_session, interaction=admin,
                                         author_id=555, user_id=666, adding=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "secure", "secure_batch"])
async def test_first_collaborator_upload_keeps_original_author(db_session, mode):
    """验证各类首次协作者上传均将原帖主保存为作者。"""
    # 准备协作者身份和普通消息、仓库及附件的模拟上传依赖。
    await grant(db_session)
    upload, _ = services()
    actor = interaction()
    actor.channel.fetch_message = AsyncMock(return_value=SimpleNamespace(
        id=99, attachments=[], content="资源",
    ))
    warehouse = MagicMock(spec=discord.Thread)
    warehouse.send = AsyncMock(return_value=SimpleNamespace(id=99))
    upload._find_or_create_warehouse_thread = AsyncMock(return_value=warehouse)
    file = MagicMock(spec=discord.Attachment)
    file.filename = "resource.zip"
    file.to_file = AsyncMock(return_value=MagicMock(spec=discord.File))
    # 分别覆盖普通上传、受保护上传和消息附件批量上传。
    if mode == "secure_batch":
        result = await upload.handle_secure_upload_submission_from_message(
            db_session, interaction=actor, attachments=[file], version_info="v1", password=None,
        )
    else:
        result = await upload.handle_upload_submission(
            db_session, interaction=actor, mode=mode, version_info="v1", password=None,
            file=file if mode == "secure" else None,
            message_link="https://discord.com/channels/1/10/99" if mode == "normal" else None,
        )
    # 成功后核对作者归属和资源数量，防止将操作人误写为作者。
    assert "成功" in result
    thread = await ThreadRepository().get_by_public_thread_id(db_session, public_thread_id=10)
    assert thread.author_id == 100 and thread.guild_id == 1
    assert len(await ResourceRepository().get_by_thread_id(db_session, thread_id=thread.id)) == 1


@pytest.mark.asyncio
async def test_collaborator_uses_own_privacy_record(db_session):
    """验证协作者不能复用原作者的隐私协议同意记录。"""
    # 仅让原作者同意协议，协作者保持没有同意记录的状态。
    await grant(db_session)
    db_session.add(User(id=100, has_agreed_to_privacy_policy=True))
    await db_session.commit()
    upload, _ = services()
    result = await upload.handle_upload(db_session, interaction=interaction(), mode="normal")
    # 上传应进入协作者自己的协议确认界面并创建独立用户记录。
    assert result["view"].user_id == 200
    user = await UserRepository().get(db_session, id=200)
    assert user.has_agreed_to_privacy_policy is False


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["edit", "replace", "delete", "quick"])
async def test_resource_writes_allowed_then_denied_after_revoke(db_session, operation):
    """验证资源写入在授权撤销或帖子上下文不符时被拒绝。"""
    # 准备已授权协作者、受保护资源和模拟仓库文件操作。
    await grant(db_session)
    _, manage = services()
    thread = Thread(public_thread_id=10, author_id=100, guild_id=1, warehouse_thread_id=20)
    db_session.add(thread)
    await db_session.flush()
    resource = Resource(thread_id=thread.id, version_info="v1", upload_mode=UploadMode.SECURE,
                        source_message_id=99, filename="old.zip")
    db_session.add(resource)
    await db_session.commit()
    actor = interaction()
    warehouse = MagicMock(spec=discord.Thread)
    warehouse.send = AsyncMock(return_value=SimpleNamespace(id=101))
    warehouse.fetch_message = AsyncMock(return_value=SimpleNamespace(delete=AsyncMock()))
    manage.bot.fetch_channel = AsyncMock(return_value=warehouse)
    attachment = SimpleNamespace(title=None, filename="new.zip",
                                 to_file=AsyncMock(side_effect=lambda **kw: discord.File(
                                     io.BytesIO(b"resource"), filename=kw["filename"])))

    async def execute():
        """执行当前参数指定的操作入口以复用授权前后的检查。"""
        # 所有分支都携带同一交互上下文，验证服务写入时独立复核权限。
        if operation == "edit":
            return await manage.update_resource(db_session, resource_id=resource.id,
                                                interaction=actor, version_info="v2", password="pw")
        if operation == "replace":
            return await manage.replace_resource_source(
                db_session, resource_id=resource.id, interaction=actor, attachment=attachment,
                expected_source_message_id=resource.source_message_id,
            )
        if operation == "delete":
            return await manage.delete_resource(db_session, resource_id=resource.id, interaction=actor)
        return await manage.toggle_quick_mode(db_session, thread_id=thread.id, interaction=actor)

    # 授权有效时允许写入，删除场景需重建资源以继续验证撤销。
    await execute()
    await db_session.commit()
    if operation == "delete":
        resource = Resource(thread_id=thread.id, version_info="v1", upload_mode=UploadMode.SECURE,
                            source_message_id=102, filename="old.zip")
        db_session.add(resource)
        await db_session.commit()
    # 提交撤销后重试同一操作，必须在文件处理和仓库访问前拒绝。
    await CollaboratorRepository().remove(db_session, author_id=100, user_id=200)
    await db_session.commit()
    manage.bot.fetch_channel.reset_mock()
    attachment.to_file.reset_mock()
    with pytest.raises(PermissionError):
        await execute()
    manage.bot.fetch_channel.assert_not_awaited()
    attachment.to_file.assert_not_awaited()
    # 即使操作人为原作者，也不能从不属于该资源的帖子写入。
    actor.channel.id = 11
    actor.user.id = 100
    with pytest.raises(PermissionError):
        await execute()


@pytest.mark.asyncio
async def test_open_panels_and_modals_stop_working_after_revoke(db_session, monkeypatch):
    """验证撤销授权后已打开的管理面板和表单失效。"""
    # 在授权有效时准备资源并打开管理面板和编辑、换源表单。
    await grant(db_session)
    upload, manage = services()
    original = interaction()
    thread = await upload._get_or_create_thread(db_session, interaction=original)
    resource = Resource(thread_id=thread.id, version_info="v1", upload_mode=UploadMode.SECURE,
                        source_message_id=99, filename="old.zip")
    db_session.add(resource)
    await db_session.commit()
    # 界面回调使用独立会话，从数据库读取最新授权状态。
    monkeypatch.setattr("src.ui.management_ui.AsyncSessionLocal",
                        async_sessionmaker(db_session.bind, expire_on_commit=False))
    panel = (await manage.handle_management_request(db_session, interaction=original, selected_resource_id=resource.id))["view"]
    resource_id = resource.id
    await db_session.commit()
    # 脱离已有会话缓存，模拟界面在后续请求中持有旧资源对象。
    db_session.expunge_all()
    edit = ManagementModal(resource, manage, panel)
    edit.version_info_input._value = "changed"
    replace = ReplaceSourceModal(resource, panel)
    await panel.delete_button.callback(original)
    delete_view = original.response.edit_message.call_args.kwargs["view"]
    assert await panel.interaction_check(original)
    # 撤销后依次触发旧面板、删除确认、快捷模式和编辑表单。
    await CollaboratorRepository().remove(db_session, author_id=100, user_id=200)
    await db_session.commit()
    assert not await panel.interaction_check(original)
    assert not await delete_view.interaction_check(original)
    await delete_view.confirm_delete.callback(original)
    await panel.toggle_quick_mode_button.callback(original)
    await edit.on_submit(original)
    # 换源表单必须先复核权限，再处理用户上传的替换文件。
    replacement = MagicMock(spec=discord.Attachment)
    replacement.to_file = AsyncMock()
    replace.file_input._values = [replacement]
    await replace.on_submit(original)
    replacement.to_file.assert_not_awaited()
    # 确认错误通过私密提示返回，其他用户也无法接管面板。
    assert any("权限不足" in str(call) for call in original.followup.send.call_args_list)
    assert not await panel.interaction_check(interaction(user=300))
    # 最终资源信息和来源保持原值，证明旧表单未产生写入。
    resource = await db_session.get(Resource, resource_id)
    assert resource.version_info == "v1" and resource.source_message_id == 99


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
async def test_old_upload_submission_denied_after_revoke(db_session, batch):
    """验证撤销授权后旧上传表单不能写入资源或处理文件。"""
    # 先创建授权期内的帖子上下文，再撤销协作者权限。
    await grant(db_session)
    upload, _ = services()
    actor = interaction()
    await upload._get_or_create_thread(db_session, interaction=actor)
    await db_session.commit()
    await CollaboratorRepository().remove(db_session, author_id=100, user_id=200)
    await db_session.commit()
    file = MagicMock(spec=discord.Attachment)
    file.to_file = AsyncMock()
    file.filename = "file.zip"
    # 分别提交消息附件批量上传与单文件上传，模拟尚未提交的旧表单。
    if batch:
        result = await upload.handle_secure_upload_submission_from_message(
            db_session, interaction=actor, attachments=[file], version_info="v1", password=None,
        )
    else:
        result = await upload.handle_upload_submission(
            db_session, interaction=actor, mode="secure", file=file,
            version_info="v1", password=None,
        )
    # 拒绝发生在读取附件前，数据库也不能留下资源记录。
    assert "权限不足" in result
    file.to_file.assert_not_awaited()
    assert (await db_session.execute(select(func.count()).select_from(Resource))).scalar_one() == 0


@pytest.mark.asyncio
async def test_list_any_author_pagination_and_command_permissions(db_session, monkeypatch):
    """验证任意名单查询、私密分页及查询与修改的权限边界。"""
    # 准备超过一页的名单，并使用没有授权的用户查询其他作者。
    for user in range(200, 221):
        await CollaboratorRepository().add(db_session, author_id=100, user_id=user, granted_by=999)
    await db_session.commit()
    monkeypatch.setattr("src.cogs.collaborator_cog.AsyncSessionLocal", sessions(db_session))
    cog = CollaboratorCog(MagicMock())
    viewer = interaction(user=555)
    await cog.list_collaborators.callback(cog, viewer, "100")
    sent = viewer.followup.send.call_args.kwargs
    # 查询结果必须私密返回，展示人数而不暴露授权操作人。
    assert sent["ephemeral"] is True
    assert "共 21 人" in sent["embed"].footer.text
    assert "999" not in sent["embed"].description
    # 验证末页、翻页按钮和仅限发起人操作的面板限制。
    view = sent["view"]
    assert view.previous.disabled and not view.next_page.disabled
    await view.next_page.callback(viewer)
    assert "220" in viewer.edit_original_response.call_args.kwargs["embed"].description
    assert view.next_page.disabled
    assert not await view.interaction_check(interaction(user=556))
    # 查询他人的名单不会赋予修改他人授权的权限。
    viewer.followup.send.reset_mock()
    await cog._change(viewer, user="<@300>", author="100", adding=True)
    assert "只有原作者" in viewer.followup.send.call_args.args[0]
    assert not await cog.service.repo.exists(db_session, author_id=100, user_id=300)
    # 移除末页最后一条记录后，面板应退回仍存在的有效页。
    await cog.service.repo.remove(db_session, author_id=100, user_id=220)
    await db_session.commit()
    embed = await view.render()
    assert view.page == 0 and "共 20 人" in embed.footer.text
    # 检查非法账号提示，以及普通用户添加自己协作者的权限。
    await cog._change(viewer, user="0", author=None, adding=True)
    assert "有效范围" in viewer.followup.send.call_args.args[0]
    await cog._change(viewer, user="<@666>", author=None, adding=True)
    assert await cog.service.repo.exists(db_session, author_id=555, user_id=666)


def test_commands_are_guild_only_with_string_ids():
    """验证协作者命令仅限服务器使用且账号参数为字符串。"""
    # 检查注册后的命令元数据，确保数字账号和失联账号可用字符串输入。
    group = CollaboratorCog(MagicMock()).__cog_app_commands__[0]
    assert group.guild_only
    assert {command.name for command in group.commands} == {"添加", "移除", "列表"}
    assert all(parameter.type is discord.AppCommandOptionType.string
               for command in group.commands for parameter in command.parameters)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["normal_slash", "secure_slash", "normal_menu", "secure_menu"])
async def test_upload_command_entries_check_collaborator_and_revocation(db_session, monkeypatch, entry):
    """验证斜杠和右键上传入口均检查协作者授权及其撤销。"""
    from src.cogs import upload_cog

    # 注册上传模块并准备斜杠、右键入口共用的模拟表单和消息。
    await grant(db_session)
    monkeypatch.setattr(upload_cog, "AsyncSessionLocal", sessions(db_session))
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    modal = discord.ui.Modal(title="上传")
    bot.upload_service.handle_upload = AsyncMock(return_value=modal)
    bot.upload_service.handle_secure_upload_from_message = AsyncMock(return_value=modal)
    await upload_cog.setup(bot)
    cog = bot.add_cog.call_args.args[0]
    bot.get_cog.return_value = cog
    commands = {call.args[0].name: call.args[0] for call in bot.tree.add_command.call_args_list}
    actor = interaction()
    message = MagicMock(spec=discord.Message)
    message.attachments = [MagicMock(spec=discord.Attachment)]
    message.jump_url = "https://discord.com/channels/1/10/99"

    async def execute():
        """执行当前参数指定的操作入口以复用授权前后的检查。"""
        # 按参数选择斜杠或右键入口，保持操作人和原始消息一致。
        if entry.endswith("slash"):
            await cog._start_upload(actor, mode="normal" if entry.startswith("normal") else "secure",
                                    file=message.attachments[0], message_link=message.jump_url)
        else:
            name = "上传为普通文件" if entry.startswith("normal") else "上传为受保护资源"
            await commands[name].callback(actor, message)

    # 授权期间入口正常打开表单，撤销后不得调用上传服务。
    await execute()
    actor.response.send_modal.assert_awaited_once_with(modal)
    await CollaboratorRepository().remove(db_session, author_id=100, user_id=200)
    await db_session.commit()
    bot.upload_service.handle_upload.reset_mock()
    bot.upload_service.handle_secure_upload_from_message.reset_mock()
    await execute()
    bot.upload_service.handle_upload.assert_not_awaited()
    bot.upload_service.handle_secure_upload_from_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_database_initializes_collaborators_and_current_revision(monkeypatch):
    """验证空数据库初始化时创建授权表并标记当前迁移版本。"""
    from src.database import database
    from sqlalchemy import inspect, text

    # 使用独立内存数据库，验证初始化路径无需已有用户或帖子记录。
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(database, "engine", engine)
    try:
        await database.init_db()
        async with engine.connect() as connection:
            # 同时检查授权表和迁移版本，确保初始化结果可继续执行后续迁移。
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert "author_collaborators" in tables
            assert (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == "a9c2e5f8b1d4"
    finally:
        # 释放独立测试引擎，避免连接残留影响其他测试。
        await engine.dispose()


@pytest.mark.asyncio
async def test_warehouse_info_separates_author_and_uploader(db_session):
    """验证仓库说明分别展示原作者账号与实际上传操作人。"""
    # 模拟协作者首次创建原作者帖子的仓库说明。
    await grant(db_session)
    upload, _ = services()
    actor = interaction()
    actor.user.mention = "<@200>"
    thread = await upload._get_or_create_thread(db_session, interaction=actor)
    forum = MagicMock(spec=discord.ForumChannel)
    warehouse = MagicMock(spec=discord.Thread)
    warehouse.id = 20
    forum.create_thread = AsyncMock(return_value=SimpleNamespace(thread=warehouse))
    upload.bot.fetch_channel = AsyncMock(return_value=forum)
    upload.warehouse_channel_id = 50
    await upload._find_or_create_warehouse_thread(db_session, actor, thread)
    # 核对原作者与实际上传人分别展示，正文仍指向原作者。
    embed = forum.create_thread.call_args.kwargs["embed"]
    fields = {field.name: field.value for field in embed.fields}
    assert fields["🆔 原作者 ID"] == "`100`"
    assert fields["📤 上传操作人"] == "<@200> (`200`)"
    assert "<@100>" in embed.description


@pytest.mark.asyncio
async def test_unknown_author_upload_creates_no_records(db_session):
    """验证未知原作者的上传不会创建帖子或资源记录。"""
    # 在原生帖主缺失且数据库没有回退记录时尝试普通上传。
    upload, _ = services()
    actor = interaction(owner=None)
    result = await upload.handle_upload_submission(
        db_session, interaction=actor, mode="normal", version_info="v1", password=None,
        message_link="https://discord.com/channels/1/10/99",
    )
    # 明确拒绝未知作者操作，同时确保未产生任何帖子或资源记录。
    assert "无法确认原帖主" in result
    assert (await db_session.execute(select(func.count()).select_from(Thread))).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(Resource))).scalar_one() == 0


@pytest.mark.asyncio
async def test_commands_reject_dms_and_allow_empty_public_list(db_session, monkeypatch):
    """验证私聊命令被拒绝且任意作者的空名单可被查询。"""
    monkeypatch.setattr("src.cogs.collaborator_cog.AsyncSessionLocal", sessions(db_session))
    cog = CollaboratorCog(MagicMock())
    viewer = interaction(user=555)
    # 绕过命令注册限制直接调用入口，验证执行阶段仍拒绝私聊。
    viewer.guild = None
    await cog._change(viewer, user="200", author="100", adding=True)
    await cog.list_collaborators.callback(cog, viewer, "100")
    assert viewer.response.send_message.await_count == 2
    # 服务器用户可查询无授权记录的作者，空名单不能继续翻页。
    viewer = interaction(user=555)
    await cog.list_collaborators.callback(cog, viewer, "987")
    sent = viewer.followup.send.call_args.kwargs
    assert "暂无协作者" in sent["embed"].description
    assert sent["view"].previous.disabled and sent["view"].next_page.disabled
