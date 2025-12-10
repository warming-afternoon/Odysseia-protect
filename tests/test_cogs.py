import pytest
import discord
from unittest.mock import AsyncMock, MagicMock, patch

from src.cogs.upload_cog import UploadCog


@pytest.mark.asyncio
class TestUploadCog:
    """测试 UploadCog 的功能。"""

    async def test_handle_service_result_with_embed_only(self):
        """测试 _handle_service_result 处理仅包含 embed 的字典。"""
        cog = UploadCog(bot=MagicMock())
        mock_interaction = AsyncMock()
        embed = discord.Embed(title="测试 Embed")
        result = {"embed": embed}

        await cog._handle_service_result(mock_interaction, result)

        # 应调用 send_message 并仅包含 embed
        mock_interaction.response.send_message.assert_called_once_with(
            embed=embed, ephemeral=True
        )

    async def test_handle_service_result_with_embed_and_view(self):
        """测试 _handle_service_result 处理同时包含 embed 和 view 的字典。"""
        cog = UploadCog(bot=MagicMock())
        mock_interaction = AsyncMock()
        embed = discord.Embed(title="测试 Embed")
        view = discord.ui.View()
        result = {"embed": embed, "view": view}

        await cog._handle_service_result(mock_interaction, result)

        mock_interaction.response.send_message.assert_called_once_with(
            embed=embed, view=view, ephemeral=True
        )

    async def test_handle_service_result_with_modal(self):
        """测试 _handle_service_result 处理 Modal。"""
        cog = UploadCog(bot=MagicMock())
        mock_interaction = AsyncMock()
        modal = discord.ui.Modal(title="测试 Modal")
        result = modal

        await cog._handle_service_result(mock_interaction, result)

        mock_interaction.response.send_modal.assert_called_once_with(modal)

    async def test_handle_service_result_with_invalid_dict(self):
        """测试 _handle_service_result 处理无效字典（无 embed）。"""
        cog = UploadCog(bot=MagicMock())
        mock_interaction = AsyncMock()
        result = {"foo": "bar"}

        await cog._handle_service_result(mock_interaction, result)

        mock_interaction.response.send_message.assert_called_once_with(
            "发生未知错误，无法显示响应。", ephemeral=True
        )

    async def test_handle_service_result_with_permission_denied_embed(self):
        """测试权限不足的 embed（仅包含 embed 的字典）能正确显示。"""
        cog = UploadCog(bot=MagicMock())
        mock_interaction = AsyncMock()
        embed = discord.Embed(
            title="🚫 权限不足",
            description="抱歉，只有本帖的作者才能上传资源。",
            color=discord.Color.red(),
        )
        result = {"embed": embed}

        await cog._handle_service_result(mock_interaction, result)

        # 应发送 embed，无 view
        mock_interaction.response.send_message.assert_called_once_with(
            embed=embed, ephemeral=True
        )
        # 确保没有调用 send_modal
        mock_interaction.response.send_modal.assert_not_called()
