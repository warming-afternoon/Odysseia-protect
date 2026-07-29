"""
集成测试，验证多个服务之间的协作。
"""

import pytest
import discord
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.repositories.resource import ResourceRepository
from src.database.repositories.thread import ThreadRepository
from src.database.repositories.user import UserRepository
from src.database.schemas import ThreadCreate, UserCreate
from src.database.models import UploadMode
from src.services.upload_service import UploadService
from src.services.download_service import DownloadService
from src.services.management_service import ManagementService
from src.utils.auth import assert_thread_author


@pytest.mark.asyncio
class TestIntegration:
    """集成测试套件。"""

    async def test_upload_and_download_flow(self, db_session: AsyncSession):
        """测试上传后下载的完整流程。"""
        # 1. 初始化仓库和服务
        thread_repo = ThreadRepository()
        resource_repo = ResourceRepository()
        user_repo = UserRepository()
        mock_bot = MagicMock()
        mock_bot.download_service.ensure_download_entry = AsyncMock()
        upload_service = UploadService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )
        download_service = DownloadService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )

        # 2. 创建用户并同意隐私协议
        user_data = UserCreate(id=999, has_agreed_to_privacy_policy=True)
        await user_repo.create(db_session, obj_in=user_data)
        await db_session.commit()

        # 3. 模拟交互（作者）
        mock_channel = MagicMock(spec=discord.Thread)
        mock_channel.id = 12345
        mock_channel.name = "Test Thread"

        mock_interaction = MagicMock()
        mock_interaction.user.id = 999
        mock_interaction.channel = mock_channel
        mock_interaction.guild = MagicMock(spec=discord.Guild)
        mock_interaction.guild.id = 111

        # 4. 模拟附件（普通文件上传需要消息链接）
        # 由于普通文件上传需要消息链接，我们模拟一个有效的链接
        # 但为了简化，我们直接模拟服务返回成功字符串（因为实际处理需要 Discord API）
        # 相反，我们测试服务层直接调用 handle_upload_submission
        # 让我们创建一个帖子记录，然后模拟上传提交
        thread_data = ThreadCreate(
            public_thread_id=12345,
            author_id=999,
            warehouse_thread_id=None,
        )
        thread = await thread_repo.create(db_session, obj_in=thread_data)
        await db_session.flush()

        # 模拟一个消息链接（格式为 "https://discord.com/channels/...")
        # 由于我们不想实际获取消息，我们模拟 parse_message_link 返回有效值
        with patch("src.services.upload_service.parse_message_link") as mock_parse:
            mock_parse.return_value = (
                111,
                12345,
                67890,
            )  # guild_id, channel_id, message_id
            # 模拟 fetch_message 返回一个带有附件和内容的消息
            mock_message = AsyncMock()
            mock_message.attachments = []
            mock_message.content = "测试内容"
            mock_channel.fetch_message.return_value = mock_message

            # 调用 handle_upload_submission（这是模态框提交后的方法）
            result = await upload_service.handle_upload_submission(
                session=db_session,
                interaction=mock_interaction,
                mode="normal",
                version_info="1.0",
                password=None,
                file=None,
                message_link="https://discord.com/channels/111/12345/67890",
            )
            # 期望返回成功消息
            assert "成功" in result or "✅" in result

        # 5. 验证资源已创建
        resources = await resource_repo.get_by_thread_id(
            db_session, thread_id=thread.id
        )
        assert len(resources) == 1
        resource = resources[0]
        assert resource.upload_mode == UploadMode.NORMAL

        # 6. 测试下载请求
        download_result = await download_service.handle_download_request(
            session=db_session,
            source=mock_interaction,
        )
        assert "embed" in download_result
        assert "view" in download_result

        # 7. 测试管理请求（作者）
        management_service = ManagementService(
            bot=mock_bot,
            resource_repo=resource_repo,
            thread_repo=thread_repo,
            user_repo=user_repo,
        )
        management_result = await management_service.handle_management_request(
            session=db_session,
            interaction=mock_interaction,
        )
        assert "embed" in management_result
        assert "view" in management_result

        # 8. 清理（可选）
        # 测试通过

    async def test_non_author_is_rejected_before_upload(
        self, db_session: AsyncSession
    ):
        """测试上传入口在调用服务前拒绝非帖子作者。"""
        thread_repo = ThreadRepository()
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
        mock_channel.owner_id = None
        mock_interaction = MagicMock()
        mock_interaction.user.id = 222
        mock_interaction.channel = mock_channel
        mock_interaction.response.send_message = AsyncMock()

        allowed = await assert_thread_author(
            db_session,
            interaction=mock_interaction,
            thread_repo=thread_repo,
        )

        assert allowed is False
        mock_interaction.response.send_message.assert_awaited_once_with(
            "❌ 权限不足：只有本帖的作者才能执行此操作。",
            ephemeral=True,
        )
