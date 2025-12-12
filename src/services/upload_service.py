# -*- coding: utf-8 -*-
"""
上传服务，负责处理文件上传相关的业务逻辑。
"""

import logging
from typing import Any, Optional, Union

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import PRIVACY_POLICY_TEXT
from src.database.models import UploadMode
from src.database.schemas import ResourceCreate, ThreadCreate, UserCreate
from src.services.base import BaseService
from src.ui.upload_ui import PrivacyPolicyView, NormalUploadModal, SecureUploadModal
from src.utils.discord_utils import parse_message_link

logger = logging.getLogger(__name__)


class UploadService(BaseService):
    """封装了所有与资源上传相关的业务逻辑。"""

    async def _get_or_create_user(self, session: AsyncSession, *, user_id: int):
        """获取或创建用户记录。"""
        user = await self.user_repo.get(session, id=user_id)
        if not user:
            user_data = UserCreate(id=user_id, has_agreed_to_privacy_policy=False)
            user = await self.user_repo.create(session, obj_in=user_data)
            await session.flush()  # 确保 ID 可用
        return user

    async def _get_or_create_thread(
        self, session: AsyncSession, *, interaction: discord.Interaction
    ):
        """根据 Discord 频道对象，获取或创建数据库中的帖子记录。"""
        if not interaction.channel:
            raise ValueError("Interaction channel is missing.")

        thread_model = await self.thread_repo.get_by_public_thread_id(
            session, public_thread_id=interaction.channel.id
        )

        if not thread_model:
            logger.info(f"帖子 {interaction.channel.id} 不存在，将创建新记录。")
            author_id = interaction.user.id
            thread_data = ThreadCreate(
                public_thread_id=interaction.channel.id,
                author_id=author_id,
                warehouse_thread_id=None,
            )
            thread_model = await self.thread_repo.create(session, obj_in=thread_data)
            await session.flush()
            logger.info(f"已为帖子 {interaction.channel.id} 创建数据库记录。")

        return thread_model

    async def handle_upload(
        self,
        session: AsyncSession,
        *,
        interaction: discord.Interaction,
        mode: str,
        file: Optional[discord.Attachment] = None,
        message_link: Optional[str] = None,
    ) -> Union[dict[str, Any], NormalUploadModal, SecureUploadModal]:
        """
        处理上传命令的初始入口。
        检查隐私协议，如果通过，则返回一个模态框供用户填写详细信息。
        """
        if not interaction.channel or not isinstance(
            interaction.channel, (discord.TextChannel, discord.Thread)
        ):
            return {
                "embed": discord.Embed(
                    title="❌ 操作无效",
                    description="此命令只能在服务器的文本频道或帖子中使用。",
                    color=discord.Color.red(),
                )
            }

        author = interaction.user

        # --- 隐私协议检查 ---
        user = await self._get_or_create_user(session, user_id=author.id)
        if not user.has_agreed_to_privacy_policy:
            logger.info(f"用户 {author.id} 尚未同意隐私协议，将向其显示协议。")
            await session.commit()
            embed = discord.Embed(
                title="📜 请阅读并同意隐私协议",
                description=PRIVACY_POLICY_TEXT,
                color=discord.Color.blue(),
            )
            view = PrivacyPolicyView(
                user_repo=self.user_repo,
                service=self,
                mode=mode,
                file=file,
                message_link=message_link,
            )
            return {"embed": embed, "view": view}

        # 用户已同意，根据模式返回不同的模态框
        if mode == "secure":
            assert file is not None
            return SecureUploadModal(service=self, files=file)
        else:  # normal mode
            return NormalUploadModal(service=self, message_link=message_link)

    async def handle_secure_upload_from_message(
        self,
        session: AsyncSession,
        *,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> Union[dict[str, Any], SecureUploadModal]:
        """从消息上下文菜单开始受保护文件的上传流程，返回一个模态框。"""

        # 对于上下文菜单，我们跳过隐私协议检查，直接返回模态框
        return SecureUploadModal(
            service=self, files=message.attachments, source_message=message
        )

    async def handle_upload_submission(
        self,
        session: AsyncSession,
        *,
        interaction: discord.Interaction,
        mode: str,
        version_info: str,
        password: Optional[str],
        file: Optional[discord.Attachment] = None,
        message_link: Optional[str] = None,
    ) -> str:
        """处理来自 UploadModal 的提交，完成文件上传的最终逻辑。"""
        if not interaction.channel or not isinstance(
            interaction.channel, (discord.TextChannel, discord.Thread)
        ):
            return "错误：此命令似乎在无效的频道上下文中被调用。"

        author = interaction.user
        log_identifier = file.filename if file else message_link
        logger.info(
            f"用户 {author} ({author.id}) 在频道 {interaction.channel.id} 提交上传表单: {log_identifier}, 模式: {mode}"
        )

        try:
            # --- 权限检查（从 handle_upload 移至此处） ---
            thread_model = await self._get_or_create_thread(
                session, interaction=interaction
            )
            if thread_model.author_id != interaction.user.id:
                return "🚫 **权限不足**\n抱歉，只有本帖的作者才能上传资源。"

            if mode == "secure":
                # 断言 file 存在，因为 Cog 层已经校验过
                assert file is not None
                result = await self._handle_secure_upload(
                    session,
                    interaction=interaction,
                    file=file,
                    version_info=version_info,
                    password=password,
                )
            else:
                # 断言 message_link 存在
                assert message_link is not None
                result = await self._handle_normal_upload(
                    session,
                    interaction=interaction,
                    message_link=message_link,
                    version_info=version_info,
                    password=password,
                )
            # 只有在所有数据库操作成功后才提交事务
            await session.commit()
            return result
        except Exception as e:
            log_identifier_on_error = file.filename if file else "N/A"
            logger.error(
                f"处理上传提交时发生严重错误，将回滚事务。用户: {author.id}, 文件: {log_identifier_on_error}",
                exc_info=e,
            )
            # 如果发生任何错误，回滚所有数据库更改
            await session.rollback()
            return "❌ 上传过程中发生了一个未知的内部错误，操作已被取消。请联系管理员。"

    async def _find_or_create_warehouse_thread(
        self,
        session: AsyncSession,
        interaction: discord.Interaction,
        thread_model,
    ) -> discord.Thread:
        """查找或创建一个与公开帖子关联的私密仓库帖子，确保逻辑统一。"""
        # 1. 检查仓库频道是否已配置
        if not self.warehouse_channel_id:
            raise ValueError("管理员未配置仓库频道，受保护文件功能当前不可用。")

        # 2. 获取仓库论坛频道并验证其类型
        try:
            warehouse_forum = await self.bot.fetch_channel(self.warehouse_channel_id)
            if not isinstance(warehouse_forum, discord.ForumChannel):
                logger.error(
                    f"仓库频道ID {self.warehouse_channel_id} 是一个 "
                    f"'{type(warehouse_forum).__name__}'，而不是预期的论坛频道。"
                )
                raise ValueError("服务器内部配置错误（仓库频道必须是论坛）。")
        except (discord.NotFound, discord.Forbidden) as e:
            logger.error(f"无法访问仓库论坛频道 {self.warehouse_channel_id}: {e}")
            raise ValueError("无法访问仓库频道，请管理员检查ID和Bot权限。")

        # 3. 尝试获取已存在的仓库帖子
        warehouse_thread = None
        if thread_model.warehouse_thread_id:
            try:
                warehouse_thread = await self.bot.fetch_channel(
                    thread_model.warehouse_thread_id
                )
                if not isinstance(warehouse_thread, discord.Thread):
                    warehouse_thread = None  # 如果ID指向的不是帖子，则重新创建
            except discord.NotFound:
                logger.warning(
                    f"仓库帖子 {thread_model.warehouse_thread_id} 在Discord中找不到了，将创建一个新的。"
                )

        # 4. 如果不存在，则创建新的仓库帖子
        if not warehouse_thread:
            try:
                # 断言 interaction.channel 是支持 .name 和 .id 的类型
                assert isinstance(
                    interaction.channel, (discord.TextChannel, discord.Thread)
                )
                public_name = (
                    interaction.channel.name
                    if hasattr(interaction.channel, "name")
                    else interaction.channel.id
                )
                new_thread_name = f"📦 仓库 | {public_name}"

                # 创建一个信息丰富的 Embed 作为启动消息
                author = interaction.user
                embed = discord.Embed(
                    title="📦 安全仓库关联信息",
                    description=f"此仓库与作者 {author.mention} 的上传相关联。",
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(
                    name="🔗 公开帖子",
                    value=f"[{interaction.channel.name}]({interaction.channel.jump_url})",
                    inline=False,
                )
                embed.add_field(
                    name="🆔 公开帖子 ID",
                    value=f"`{interaction.channel.id}`",
                    inline=False,
                )
                embed.add_field(name="👤 作者", value=f"`{str(author)}`", inline=True)
                embed.add_field(name="🆔 作者 ID", value=f"`{author.id}`", inline=True)

                # 创建帖子并发送 Embed
                thread_with_message = await warehouse_forum.create_thread(
                    name=new_thread_name, embed=embed
                )
                warehouse_thread = thread_with_message.thread
                # 更新数据库
                await self.thread_repo.update(
                    session,
                    db_obj=thread_model,
                    obj_in={"warehouse_thread_id": warehouse_thread.id},
                )
                await session.flush()  # 确保更新能被同一事务中的后续操作看到
            except discord.HTTPException as e:
                logger.error(f"在仓库论坛 {warehouse_forum.id} 中创建帖子失败: {e}")
                raise IOError("创建安全存储帖子失败。")

        # 5. 断言并返回
        assert isinstance(warehouse_thread, discord.Thread)
        return warehouse_thread

    async def _handle_secure_upload(
        self,
        session: AsyncSession,
        *,
        interaction: discord.Interaction,
        file: discord.Attachment,
        version_info: Optional[str],
        password: Optional[str],
    ) -> str:
        """处理受保护文件的上传逻辑，文件将被上传到私密的论坛帖子中。"""
        assert isinstance(interaction.channel, (discord.TextChannel, discord.Thread))
        try:
            # 1. 获取或创建当前公开帖子的数据库记录
            thread_model = await self._get_or_create_thread(
                session, interaction=interaction
            )

            # 2. 统一调用函数来查找或创建仓库帖子
            warehouse_thread = await self._find_or_create_warehouse_thread(
                session, interaction, thread_model
            )

            # 3. 将文件上传到仓库帖子
            message = await warehouse_thread.send(file=await file.to_file())

            # 4. 在数据库中创建资源记录
            resource_data = ResourceCreate(
                thread_id=thread_model.id,
                upload_mode=UploadMode.SECURE,
                filename=file.filename,
                version_info=version_info or "未提供",
                source_message_id=message.id,
                password=password,
            )
            await self.resource_repo.create(session, obj_in=resource_data)

            logger.info(f"受保护文件上传成功: {file.filename} -> {warehouse_thread.id}")
            return f"✅ 受保护文件上传成功！文件 `{file.filename}` 已被安全存储。"
        except (ValueError, IOError, discord.HTTPException) as e:
            logger.error(f"处理受保护文件上传时失败: {e}")
            return f"❌ 错误: {e}"

    async def handle_secure_upload_submission_from_message(
        self,
        session: AsyncSession,
        *,
        interaction: discord.Interaction,
        attachments: list[discord.Attachment],
        version_info: str,
        password: Optional[str],
        source_message: Optional[discord.Message] = None,
    ) -> str:
        """处理来自多附件上传模态框的提交。"""
        try:
            thread_model = await self._get_or_create_thread(
                session, interaction=interaction
            )
            result_message = (
                await self._handle_secure_upload_submission_from_attachments(
                    session,
                    interaction=interaction,
                    attachments=attachments,
                    version_info=version_info,
                    password=password,
                    thread_model=thread_model,
                )
            )
            await session.commit()

            # --- 新逻辑：快捷模式处理 ---
            if source_message:  # 仅当从上下文菜单调用时才处理
                if thread_model.quick_mode_enabled:
                    try:
                        await source_message.delete()
                        logger.info(
                            f"快捷模式开启：已自动删除源消息 {source_message.id}"
                        )
                        result_message += "\n⚡️ 快捷模式已开启，原始消息已自动删除。"
                    except (discord.Forbidden, discord.NotFound) as e:
                        logger.warning(
                            f"快捷模式：删除源消息 {source_message.id} 失败: {e}"
                        )
                else:
                    try:
                        # 断言 interaction.channel 是一个帖子，以便安全地访问 .name 属性
                        assert isinstance(interaction.channel, discord.Thread)
                        dm_channel = await source_message.author.create_dm()
                        embed = discord.Embed(
                            title="📎 文件转存成功",
                            description=(
                                f"您在帖子 **{interaction.channel.name}** 中的消息已成功转存为受保护资源。\n\n"
                                f"🔗 [点击跳转到原始消息]({source_message.jump_url})\n\n"
                                "请及时手动删除该原始消息。\n"
                                "如果您希望以后自动删除，可以在该帖子中使用 `/管理` 命令开启 **快捷模式**。"
                            ),
                            color=discord.Color.green(),
                        )
                        await dm_channel.send(embed=embed)
                        logger.info(
                            f"快捷模式关闭：已私信提醒用户 {source_message.author.id} 删除源消息"
                        )
                    except discord.Forbidden:
                        logger.warning(
                            f"无法私信用户 {source_message.author.id}，可能已屏蔽Bot或关闭私信"
                        )
            # --- 结束 ---

            return result_message
        except PermissionError as e:
            logger.warning(
                f"用户 {interaction.user.id} 尝试在不属于他们的帖子中上传: {e}"
            )
            await session.rollback()
            return f"🚫 **权限不足**\n{e}"
        except Exception as e:
            logger.error(
                "处理来自消息的多附件安全上传时出错，将回滚事务。",
                exc_info=e,
            )
            await session.rollback()
            return f"❌ 上传失败: 处理附件时发生内部错误: {e}"

    async def _handle_secure_upload_submission_from_attachments(
        self,
        session: AsyncSession,
        *,
        interaction: discord.Interaction,
        attachments: list[discord.Attachment],
        version_info: str,
        password: Optional[str],
        thread_model,
    ) -> str:
        """处理多个附件的安全上传的后端逻辑。"""
        assert isinstance(interaction.channel, (discord.TextChannel, discord.Thread))

        # 1. 权限检查 (thread_model 已从外部传入)
        if thread_model.author_id != interaction.user.id:
            raise PermissionError("抱歉，只有本帖的作者才能上传资源。")

        # 2. 统一调用函数来查找或创建仓库帖子
        warehouse_thread = await self._find_or_create_warehouse_thread(
            session, interaction, thread_model
        )

        # 3. 上传所有附件并创建资源记录
        uploaded_files = []
        for attachment in attachments:
            try:
                message = await warehouse_thread.send(file=await attachment.to_file())
                resource_data = ResourceCreate(
                    thread_id=thread_model.id,
                    upload_mode=UploadMode.SECURE,
                    filename=attachment.filename,
                    version_info=version_info,
                    source_message_id=message.id,
                    password=password,
                )
                await self.resource_repo.create(session, obj_in=resource_data)
                uploaded_files.append(attachment.filename)
            except discord.HTTPException as e:
                logger.error(
                    f"上传附件 {attachment.filename} 到仓库帖子 {warehouse_thread.id} 失败: {e}"
                )
                continue  # 跳过失败的附件

        if not uploaded_files:
            raise IOError("所有附件都上传失败。")

        return f"✅ 成功保护了 {len(uploaded_files)} 个文件:\n- " + "\n- ".join(
            f"`{f}`" for f in uploaded_files
        )

    async def _handle_normal_upload(
        self,
        session: AsyncSession,
        *,
        interaction: discord.Interaction,
        message_link: str,
        version_info: Optional[str],
        password: Optional[str],
    ) -> str:
        """
        处理普通文件的上传逻辑，核心是验证并记录一个已存在的消息。
        """
        # 断言类型以帮助 Pylance
        assert isinstance(interaction.channel, (discord.TextChannel, discord.Thread))

        # 1. 解析和验证消息链接
        parsed_ids = parse_message_link(message_link)
        if not parsed_ids:
            return "❌ **链接格式错误**\n请提供一个有效的 Discord 消息链接。"

        _guild_id, channel_id, message_id = parsed_ids

        # 2. 验证消息是否在当前帖子中
        if channel_id != interaction.channel.id:
            return "❌ **链接位置错误**\n您提供的消息链接必须指向当前帖子内的消息。"

        # 3. 获取消息
        try:
            target_message = await interaction.channel.fetch_message(message_id)
            # 根据用户反馈，不再验证附件
        except discord.NotFound:
            return (
                f"❌ **找不到消息**\n无法在当前帖子中找到 ID 为 `{message_id}` 的消息。"
            )
        except discord.Forbidden:
            return "❌ **权限不足**\nBot 没有足够的权限来读取此频道的消息历史记录。"
        except Exception as e:
            logger.error(f"获取普通文件目标消息时发生未知错误: {e}")
            return "❌ **未知错误**\n获取您提供的消息时发生内部错误。"

        # 4. 获取或创建帖子模型
        thread_model = await self._get_or_create_thread(
            session, interaction=interaction
        )

        # 5. 确定文件名
        # 如果消息有附件，使用第一个附件的文件名
        # 如果没有，使用消息内容的前50个字符
        # 如果内容也为空，使用用户提供的版本信息
        # 最后，如果都没有，提供一个默认值
        filename: str
        if target_message.attachments:
            filename = target_message.attachments[0].filename
        elif target_message.content:
            filename = (
                target_message.content[:50] + "..."
                if len(target_message.content) > 50
                else target_message.content
            )
        elif version_info:
            filename = version_info
        else:
            filename = "无标题内容"

        # 6. 创建资源记录，直接引用用户消息
        resource_data = ResourceCreate(
            thread_id=thread_model.id,
            upload_mode=UploadMode.NORMAL,
            filename=filename,
            version_info=version_info or "未提供",
            source_message_id=target_message.id,
            password=password,
        )
        await self.resource_repo.create(session, obj_in=resource_data)

        logger.info(f"普通文件记录成功: '{filename}' 引用自消息 {target_message.id}")
        return f"✅ **普通文件记录成功**\n资源 `{filename}` 的位置已被成功记录。"
