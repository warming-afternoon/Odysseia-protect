import logging

import discord

from src.dto.resource_dto import ResourceDTO

logger = logging.getLogger(__name__)

class PasswordModal(discord.ui.Modal, title="请输入下载密码"):
    """一个用于在下载前验证密码的弹出式模态框。"""

    def __init__(self, resource: ResourceDTO):
        super().__init__(timeout=300)  # 5分钟超时
        self.resource = resource

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
        if self.password_input.value != self.resource.password:
            embed = discord.Embed(
                title="❌ 密码错误",
                description="您输入的密码不正确，请重试。",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 密码正确，立即延迟响应
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # 断言 bot 实例存在
            assert isinstance(interaction.client, discord.Client)
            bot = interaction.client

            # 确定源消息所在的频道 ID
            channel_id = (
                self.resource.warehouse_thread_id or self.resource.public_thread_id
            )
            if not channel_id:
                raise ValueError("数据库中未找到该资源关联的频道ID。")

            source_channel = await bot.fetch_channel(channel_id)

            # 断言是可获取消息的频道类型
            assert isinstance(source_channel, (discord.TextChannel, discord.Thread))
            source_message = await source_channel.fetch_message(
                self.resource.source_message_id
            )

            if source_message and source_message.attachments:
                fresh_url = source_message.attachments[0].url
            else:
                raise ValueError("源消息或附件未找到")

            # 触发下载事件，供下载计数器监听
            interaction.client.dispatch("resource_downloaded", self.resource)

            # 成功获取链接，发送包含链接的 Embed
            embed = discord.Embed(
                title="✅ 密码正确",
                description=f"下载链接如下，请尽快下载：\n\n[点击这里下载]({fresh_url})",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"为资源 {self.resource.id} 获取新下载链接失败", exc_info=e)
            await interaction.followup.send(
                "❌ 抱歉，获取下载链接时发生错误。源文件可能已被删除或Bot无法访问。",
                ephemeral=True,
            )
