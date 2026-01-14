# -*- coding: utf-8 -*-
"""
下载功能的 UI 组件 (View 和 Modal)
"""

import logging
from typing import Sequence

import discord

from src.database.database import AsyncSessionLocal
from src.database.models import Resource, UploadMode
from src.database.repositories.resource import ResourceRepository

logger = logging.getLogger(__name__)


class ResourceSelectView(discord.ui.View):
    """
    一个包含版本选择下拉菜单的交互式视图。
    """

    def __init__(self, resources: Sequence[Resource]):
        super().__init__(timeout=None)

        self.add_item(self.ResourceSelect(resources))

    class ResourceSelect(discord.ui.Select):
        """
        资源选择下拉菜单。
        """

        def __init__(self, resources: Sequence[Resource]):
            options = []
            # Discord 的下拉菜单最多只能有 25 个选项
            for resource in resources[:25]:
                mode_icon = "🔒" if resource.upload_mode == UploadMode.SECURE else "📄"
                
                # 构建 label 和 description，确保不超过 Discord 的 100 字符限制
                label_text = f"{mode_icon} 版本: {resource.version_info or '未命名'}"
                if len(label_text) > 100:
                    label_text = label_text[:90] + "..."
                
                desc_text = f"文件名: {resource.filename or 'N/A'}"
                if len(desc_text) > 100:
                    desc_text = desc_text[:90] + "..."
                
                # 为每个资源创建一个选项
                option = discord.SelectOption(
                    label=label_text,
                    description=desc_text,
                    value=str(resource.id),
                )
                options.append(option)

            # 如果没有可用的选项，创建一个禁用的占位符
            if not options:
                options.append(
                    discord.SelectOption(
                        label="没有找到任何受保护的资源", value="disabled", default=True
                    )
                )

            super().__init__(
                placeholder="请选择一个受保护的版本进行下载...",
                min_values=1,
                max_values=1,
                options=options,
                disabled=not options or options[0].value == "disabled",
            )

        async def callback(self, interaction: discord.Interaction):
            """
            当用户在下拉菜单中做出选择时，这个回调函数会被触发。
            现在它会动态获取一个全新的、有时效性的下载链接。
            """
            selected_resource_id = int(self.values[0])

            async with AsyncSessionLocal() as session:
                resource_repo = ResourceRepository()
                # 使用 joinedload 预加载关联的 Thread 对象，避免额外的查询
                selected_resource = await resource_repo.get_with_thread(
                    session, id=selected_resource_id
                )

            if not selected_resource:
                await interaction.response.send_message(
                    "错误：找不到所选的资源，它可能已被删除。", ephemeral=True
                )
                return

            # # --- 反应墙验证 ---
            # thread = selected_resource.thread
            # if thread.reaction_required:
            #     # 获取当前帖子（即 interaction.channel）
            #     if not isinstance(interaction.channel, discord.Thread):
            #         # 这不应该发生，因为 /下载 命令只在帖子中可用
            #         await interaction.response.send_message(
            #             "❌ 错误：无法验证反应，因为当前频道不是帖子。", ephemeral=True
            #         )
            #         return
            #     discord_thread = interaction.channel
            #     # 获取起始消息
            #     try:
            #         # 强制从 API 获取最新的消息状态，避免缓存问题
            #         # 帖子的 ID 和它的起始消息的 ID 是相同的
            #         starter_message = await discord_thread.fetch_message(
            #             discord_thread.id
            #         )
            #     except (discord.NotFound, discord.Forbidden, Exception) as e:
            #         logger.error(f"获取帖子起始消息失败: {e}")
            #         await interaction.response.send_message(
            #             "❌ 无法验证您的反应，请稍后再试。", ephemeral=True
            #         )
            #         return
            #
            #     # 检查用户是否已做出反应
            #     user_has_reacted = False
            #     if thread.reaction_emoji:
            #         # 检查特定表情
            #         for reaction in starter_message.reactions:
            #             if str(reaction.emoji) == thread.reaction_emoji:
            #                 # 检查该用户是否已做出反应
            #                 try:
            #                     users = [
            #                         user
            #                         async for user in reaction.users()
            #                         if user.id == interaction.user.id
            #                     ]
            #                     if users:
            #                         user_has_reacted = True
            #                         break
            #                 except discord.Forbidden:
            #                     pass
            #     else:
            #         # 检查任何反应
            #         for reaction in starter_message.reactions:
            #             try:
            #                 users = [
            #                     user
            #                     async for user in reaction.users()
            #                     if user.id == interaction.user.id
            #                 ]
            #                 if users:
            #                     user_has_reacted = True
            #                     break
            #             except discord.Forbidden:
            #                 pass
            #
            #     if not user_has_reacted:
            #         emoji_info = (
            #             f" {thread.reaction_emoji}"
            #             if thread.reaction_emoji
            #             else "任意表情"
            #         )
            #         await interaction.response.send_message(
            #             f"❌ 您需要先对本帖的起始消息做出反应[{emoji_info}]才能下载此资源。",
            #             ephemeral=True,
            #         )
            #         return

            # 动态获取新的有效链接
            fresh_url = None
            try:
                # 断言 bot 实例存在
                assert isinstance(interaction.client, discord.Client)
                bot = interaction.client

                # 确定源消息所在的频道 ID
                # 如果是受保护文件，warehouse_thread_id 存在；否则用 public_thread_id
                channel_id = (
                    selected_resource.thread.warehouse_thread_id
                    or selected_resource.thread.public_thread_id
                )
                source_channel = await bot.fetch_channel(channel_id)

                # 断言是可获取消息的频道类型
                assert isinstance(source_channel, (discord.TextChannel, discord.Thread))
                source_message = await source_channel.fetch_message(
                    selected_resource.source_message_id
                )

                if source_message and source_message.attachments:
                    fresh_url = source_message.attachments[0].url
                else:
                    raise ValueError("源消息或附件未找到")

            except Exception as e:
                logger.error(
                    f"为资源 {selected_resource_id} 获取新下载链接失败", exc_info=e
                )
                await interaction.response.send_message(
                    "❌ 抱歉，获取下载链接时发生错误。源文件可能已被删除或Bot无法访问。",
                    ephemeral=True,
                )
                return

            # 下载计数
            try:
                selected_resource.download_count += 1
                session.add(selected_resource)
                await session.commit()
                logger.info(
                    f"资源 {selected_resource.id} 的下载计数已增加至 {selected_resource.download_count}"
                )
            except Exception as e:
                await session.rollback()
                logger.error(
                    f"为资源 {selected_resource.id} 增加下载计数失败", exc_info=e
                )

            if selected_resource.password:
                modal = PasswordModal(resource=selected_resource, fresh_url=fresh_url)
                await interaction.response.send_modal(modal)

            else:
                response_embed = discord.Embed(
                    title="🔗 下载链接",
                    description=f"您选择的资源下载链接如下请尽快下载：\n\n[点击这里下载]({fresh_url})",
                    color=discord.Color.green(),
                )
                await interaction.response.send_message(
                    embed=response_embed, ephemeral=True
                )


class PasswordModal(discord.ui.Modal, title="请输入下载密码"):
    """一个用于在下载前验证密码的弹出式模态框。"""

    def __init__(self, resource: Resource, fresh_url: str):
        super().__init__(timeout=180)  # 3分钟超时
        self.resource = resource
        self.fresh_url = fresh_url  # 存储新鲜的URL

        self.password_input = discord.ui.TextInput(
            label="密码",
            style=discord.TextStyle.short,
            required=True,
            min_length=1,
            placeholder="请输入该资源版本对应的下载密码",
        )
        self.add_item(self.password_input)

    async def on_submit(self, interaction: discord.Interaction):
        """当用户提交密码后，验证密码并提供下载链接或错误信息。"""
        if self.password_input.value == self.resource.password:
            embed = discord.Embed(
                title="✅ 密码正确",
                description=f"下载链接如下，请尽快下载：\n\n[点击这里下载]({self.fresh_url})",
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                title="❌ 密码错误",
                description="您输入的密码不正确，请重试。",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
