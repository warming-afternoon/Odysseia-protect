import pytest
import discord
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.upload_service import UploadService
from src.services.download_service import DownloadService
from src.services.management_service import ManagementService
from src.services.reaction_wall_service import ReactionWallService
from src.database.repositories.resource import ResourceRepository
from src.database.repositories.thread import ThreadRepository
from src.database.repositories.user import UserRepository
from src.database.schemas import ResourceCreate, ThreadCreate, UserCreate
from src.database.models import UploadMode


@pytest.mark.asyncio
class TestUploadService:
    """测试 UploadService 的功能。"""

    async def test_handle_upload_privacy_policy_first_time_user(
        self, db_session: AsyncSession
    ):
        """测试首次用户上传时显示隐私协议。"""
        # 1. 设置
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = UploadService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 2. 模拟交互
        mock_interaction = MagicMock()
        mock_interaction.user.id = 111222  # 新用户 ID
        mock_interaction.channel = MagicMock(spec=discord.Thread)

        # 3. 执行
        result = await service.handle_upload(
            session=db_session,
            interaction=mock_interaction,
            mode="normal",
            file=None,
            message_link=None,
        )

        # 4. 断言
        assert isinstance(result, dict)
        assert "embed" in result
        assert "view" in result
        assert result["embed"].title == "📜 请阅读并同意隐私协议"
        # 验证用户已创建但未同意
        user = await user_repo.get(db_session, id=111222)
        assert user is not None
        assert user.has_agreed_to_privacy_policy is False

    async def test_handle_upload_normal_mode_new_thread(self, db_session: AsyncSession):
        """测试普通模式上传，帖子不存在于数据库中。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = UploadService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 预创建用户并标记为已同意隐私协议
        user_data = UserCreate(id=12345, has_agreed_to_privacy_policy=True)
        await user_repo.create(db_session, obj_in=user_data)
        await db_session.commit()

        # 模拟交互
        mock_channel = MagicMock(spec=discord.Thread)
        mock_channel.id = 54321
        mock_channel.name = "Test Thread"

        mock_interaction = MagicMock()
        mock_interaction.id = 99999
        mock_interaction.user.id = 12345
        mock_interaction.channel = mock_channel
        mock_interaction.guild = MagicMock(spec=discord.Guild)
        mock_interaction.guild.id = 98765

        mock_attachment = MagicMock()
        mock_attachment.filename = "my_awesome_file.zip"
        mock_attachment.url = "http://discordapp.com/attachments/fake.zip"

        # 执行
        result = await service.handle_upload(
            session=db_session,
            interaction=mock_interaction,
            mode="normal",
            file=mock_attachment,
            message_link=None,
        )

        # 断言：应返回 NormalUploadModal
        from src.ui.upload_ui import NormalUploadModal

        assert isinstance(result, NormalUploadModal)

    async def test_handle_upload_permission_denied(self, db_session: AsyncSession):
        """测试非作者用户上传时返回权限不足。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = UploadService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 创建作者用户并同意隐私协议
        author_user = UserCreate(id=111, has_agreed_to_privacy_policy=True)
        await user_repo.create(db_session, obj_in=author_user)
        # 创建非作者用户并同意隐私协议
        non_author_user = UserCreate(id=222, has_agreed_to_privacy_policy=True)
        await user_repo.create(db_session, obj_in=non_author_user)
        # 创建帖子记录，作者为 111
        thread_data = ThreadCreate(
            public_thread_id=999,
            author_id=111,
            warehouse_thread_id=None,
        )
        await thread_repo.create(db_session, obj_in=thread_data)
        await db_session.commit()

        # 模拟交互，用户为 222（非作者）
        mock_channel = MagicMock(spec=discord.Thread)
        mock_channel.id = 999

        mock_interaction = MagicMock()
        mock_interaction.user.id = 222  # 非作者
        mock_interaction.channel = mock_channel

        # 执行
        result = await service.handle_upload(
            session=db_session,
            interaction=mock_interaction,
            mode="normal",
            file=None,
            message_link=None,
        )

        # 断言：应返回权限不足的 embed
        assert isinstance(result, dict)
        assert "embed" in result
        assert result["embed"].title == "🚫 权限不足"
        assert "view" not in result  # 只有 embed


@pytest.mark.asyncio
class TestDownloadService:
    """测试 DownloadService 的功能。"""

    async def test_handle_download_request_no_thread(self, db_session: AsyncSession):
        """测试当帖子不存在时的下载请求。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = DownloadService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        mock_interaction = MagicMock()
        mock_interaction.channel = MagicMock(spec=discord.Thread)
        mock_interaction.channel.id = 12345

        result = await service.handle_download_request(
            session=db_session,
            interaction=mock_interaction,
        )

        assert isinstance(result, dict)
        assert "embed" in result
        assert result["embed"].title == "📂 暂无资源"

    async def test_handle_download_request_with_resources(
        self, db_session: AsyncSession
    ):
        """测试当帖子有资源时的下载请求。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = DownloadService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 创建帖子和资源
        thread_data = ThreadCreate(
            public_thread_id=555,
            author_id=100,
            warehouse_thread_id=None,
        )
        thread = await thread_repo.create(db_session, obj_in=thread_data)
        await db_session.flush()

        resource_data = ResourceCreate(
            thread_id=thread.id,
            upload_mode=UploadMode.SECURE,
            filename="test.zip",
            version_info="1.0",
            source_message_id=999,
            password=None,
        )
        await resource_repo.create(db_session, obj_in=resource_data)
        await db_session.commit()

        mock_interaction = MagicMock()
        mock_interaction.channel = MagicMock(spec=discord.Thread)
        mock_interaction.channel.id = 555

        result = await service.handle_download_request(
            session=db_session,
            interaction=mock_interaction,
        )

        assert isinstance(result, dict)
        assert "embed" in result
        assert "view" in result
        assert result["embed"].title == "📄 版本选择"


@pytest.mark.asyncio
class TestManagementService:
    """测试 ManagementService 的功能。"""

    async def test_handle_management_request_as_author(self, db_session: AsyncSession):
        """测试作者请求管理。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = ManagementService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 创建帖子，作者为 123
        thread_data = ThreadCreate(
            public_thread_id=888,
            author_id=123,
            warehouse_thread_id=None,
        )
        await thread_repo.create(db_session, obj_in=thread_data)
        await db_session.commit()

        mock_interaction = MagicMock()
        mock_interaction.channel = MagicMock(spec=discord.Thread)
        mock_interaction.channel.id = 888
        mock_interaction.user.id = 123  # 作者

        result = await service.handle_management_request(
            session=db_session,
            interaction=mock_interaction,
        )

        assert isinstance(result, dict)
        assert "view" in result
        assert "embed" in result
        assert result["embed"].title == "🛠️ 资源管理"

    async def test_handle_management_request_as_non_author(
        self, db_session: AsyncSession
    ):
        """测试非作者请求管理。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = ManagementService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 创建帖子，作者为 123
        thread_data = ThreadCreate(
            public_thread_id=888,
            author_id=123,
            warehouse_thread_id=None,
        )
        await thread_repo.create(db_session, obj_in=thread_data)
        await db_session.commit()

        mock_interaction = MagicMock()
        mock_interaction.channel = MagicMock(spec=discord.Thread)
        mock_interaction.channel.id = 888
        mock_interaction.user.id = 456  # 非作者

        result = await service.handle_management_request(
            session=db_session,
            interaction=mock_interaction,
        )

        assert isinstance(result, dict)
        assert "view" not in result or result.get("view") is None
        assert "embed" in result
        assert result["embed"].title == "🚫 权限不足"


@pytest.mark.asyncio
class TestReactionWallService:
    """测试 ReactionWallService 的功能。"""

    async def test_verify_user_reaction_without_requirement(self):
        """测试当 reaction_required 为 False 时，验证通过。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = ReactionWallService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        mock_thread = MagicMock(spec=discord.Thread)
        mock_user = MagicMock(spec=discord.User)

        # 模拟数据库帖子，reaction_required = False
        db_thread = MagicMock()
        db_thread.reaction_required = False

        result = await service.verify_user_reaction(
            thread=db_thread,
            discord_thread=mock_thread,
            user=mock_user,
        )
        assert result is True

    async def test_set_reaction_required(self, db_session: AsyncSession):
        """测试设置 reaction_required。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = ReactionWallService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 创建帖子
        thread_data = ThreadCreate(
            public_thread_id=777,
            author_id=100,
            warehouse_thread_id=None,
        )
        thread = await thread_repo.create(db_session, obj_in=thread_data)
        await db_session.commit()

        updated = await service.set_reaction_required(
            session=db_session,
            thread_id=thread.id,
            required=True,
        )
        assert updated is not None
        assert updated.reaction_required is True

    async def test_update_resource(self, db_session: AsyncSession):
        """测试更新资源信息。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = ManagementService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 创建帖子和资源
        thread_data = ThreadCreate(
            public_thread_id=999,
            author_id=100,
            warehouse_thread_id=None,
        )
        thread = await thread_repo.create(db_session, obj_in=thread_data)
        await db_session.flush()

        resource_data = ResourceCreate(
            thread_id=thread.id,
            upload_mode=UploadMode.NORMAL,
            filename="old.zip",
            version_info="1.0",
            source_message_id=111,
            password=None,
        )
        resource = await resource_repo.create(db_session, obj_in=resource_data)
        await db_session.commit()

        # 更新资源
        updated = await service.update_resource(
            session=db_session,
            resource_id=resource.id,
            version_info="2.0",
            password="newpass",
        )
        assert updated is not None
        assert updated.version_info == "2.0"
        assert updated.password == "newpass"

    async def test_delete_resource_normal(self, db_session: AsyncSession):
        """测试删除普通资源（仅删除数据库记录）。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = ManagementService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 创建帖子和资源
        thread_data = ThreadCreate(
            public_thread_id=888,
            author_id=100,
            warehouse_thread_id=None,
        )
        thread = await thread_repo.create(db_session, obj_in=thread_data)
        await db_session.flush()

        resource_data = ResourceCreate(
            thread_id=thread.id,
            upload_mode=UploadMode.NORMAL,
            filename="normal.zip",
            version_info="1.0",
            source_message_id=222,
            password=None,
        )
        resource = await resource_repo.create(db_session, obj_in=resource_data)
        await db_session.commit()

        # 删除资源
        success = await service.delete_resource(
            session=db_session,
            resource_id=resource.id,
        )
        assert success is True
        # 验证资源已删除
        deleted = await resource_repo.get(db_session, id=resource.id)
        assert deleted is None

    async def test_delete_resource_secure(self, db_session: AsyncSession):
        """测试删除受保护资源（模拟删除 Discord 消息）。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        # 模拟 bot 的 fetch_channel 和消息删除
        mock_channel = MagicMock(
            spec=discord.Thread
        )  # 使其成为 Thread 类型以通过 isinstance 检查
        mock_message = AsyncMock()
        # 将 fetch_channel 设置为 AsyncMock，使其可等待并返回 mock_channel
        mock_bot.fetch_channel = AsyncMock(return_value=mock_channel)
        mock_channel.fetch_message = AsyncMock(return_value=mock_message)
        mock_message.delete = AsyncMock()

        service = ManagementService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 创建带有仓库帖子 ID 的帖子
        thread_data = ThreadCreate(
            public_thread_id=777,
            author_id=100,
            warehouse_thread_id=123456,  # 模拟仓库帖子 ID
        )
        thread = await thread_repo.create(db_session, obj_in=thread_data)
        await db_session.flush()

        resource_data = ResourceCreate(
            thread_id=thread.id,
            upload_mode=UploadMode.SECURE,
            filename="secure.zip",
            version_info="1.0",
            source_message_id=333,
            password=None,
        )
        resource = await resource_repo.create(db_session, obj_in=resource_data)
        await db_session.commit()

        # 删除资源
        success = await service.delete_resource(
            session=db_session,
            resource_id=resource.id,
        )
        assert success is True
        # 验证资源已删除
        deleted = await resource_repo.get(db_session, id=resource.id)
        assert deleted is None
        # 验证尝试删除 Discord 消息
        mock_bot.fetch_channel.assert_called_once_with(123456)
        mock_channel.fetch_message.assert_called_once_with(333)
        mock_message.delete.assert_called_once()


@pytest.mark.asyncio
class TestReactionWallServiceExtended:
    """ReactionWallService 的额外测试。"""

    async def test_set_reaction_emoji(self, db_session: AsyncSession):
        """测试设置自定义反应表情。"""
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        service = ReactionWallService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 创建帖子
        thread_data = ThreadCreate(
            public_thread_id=555,
            author_id=100,
            warehouse_thread_id=None,
        )
        thread = await thread_repo.create(db_session, obj_in=thread_data)
        await db_session.commit()

        updated = await service.set_reaction_emoji(
            session=db_session,
            thread_id=thread.id,
            emoji="👍",
        )
        assert updated is not None
        assert updated.reaction_emoji == "👍"
