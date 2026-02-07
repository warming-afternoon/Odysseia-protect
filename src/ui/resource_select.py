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
from src.ui.password_input_modal import PasswordModal
from src.dto.resource_dto import ResourceDTO

logger = logging.getLogger(__name__)

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
        当用户在下拉菜单中做出选择时，此回调被触发。
        它会根据资源是否加密来决定下一步操作：
        - 如果有密码，则弹出密码输入模态框。
        - 如果没有密码，则延迟响应，获取链接，然后发送结果。
        """
        selected_resource_id = int(self.values[0])

        async with AsyncSessionLocal() as session:
            resource_repo = ResourceRepository()
            # 预加载关联的 Thread 对象
            selected_resource = await resource_repo.get_with_thread(
                session, id=selected_resource_id
            )

        if not selected_resource:
            await interaction.response.send_message(
                "错误：找不到所选的资源，它可能已被删除。", ephemeral=True
            )
            return

        # 将 ORM 对象转换为 DTO，避免 DetachedInstanceError
        resource_dto = ResourceDTO(
            id=selected_resource.id,
            password=selected_resource.password,
            source_message_id=selected_resource.source_message_id,
            warehouse_thread_id=selected_resource.thread.warehouse_thread_id,
            public_thread_id=selected_resource.thread.public_thread_id,
        )

        # 如果资源有密码，立即弹出模态框
        if resource_dto.password:
            modal = PasswordModal(resource=resource_dto)
            await interaction.response.send_modal(modal)
            return

        # 对于没有密码的资源
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # 断言 bot 实例存在
            assert isinstance(interaction.client, discord.Client)
            bot = interaction.client

            # 获取下载链接
            # 如果是受保护文件，warehouse_thread_id 存在；否则用 public_thread_id
            channel_id = (
                resource_dto.warehouse_thread_id or resource_dto.public_thread_id
            )
            if not channel_id:
                raise ValueError("数据库中未找到该资源关联的频道ID。")

            source_channel = await bot.fetch_channel(channel_id)
            assert isinstance(source_channel, (discord.TextChannel, discord.Thread))
            source_message = await source_channel.fetch_message(
                resource_dto.source_message_id
            )

            if source_message and source_message.attachments:
                fresh_url = source_message.attachments[0].url
            else:
                raise ValueError("源消息或附件未找到")

            # 触发下载事件，供其他组件监听（如下载计数器）
            interaction.client.dispatch("resource_downloaded", resource_dto)

            # 发送结果
            response_embed = discord.Embed(
                title="🔗 下载链接",
                description=f"您选择的资源下载链接如下请尽快下载：\n\n[点击这里下载]({fresh_url})",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=response_embed, ephemeral=True)

        except Exception as e:
            logger.error(
                f"为资源 {selected_resource_id} 获取新下载链接失败", exc_info=e
            )
            await interaction.followup.send(
                "❌ 抱歉，获取下载链接时发生错误。源文件可能已被删除或Bot无法访问。",
                ephemeral=True,
            )
