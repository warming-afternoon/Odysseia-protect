"""管理员动态溯源核验命令。"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import OdysseiaProtect

logger = logging.getLogger(__name__)


def _id_set(name: str) -> set[int]:
    result: set[int] = set()
    for value in os.getenv(name, "").split(","):
        value = value.strip()
        if value.isdigit():
            result.add(int(value))
    return result


def is_trace_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    if interaction.user.id in _id_set("TRACE_ADMIN_USER_IDS"):
        return True
    allowed_roles = _id_set("TRACE_ADMIN_ROLE_IDS")
    roles = getattr(interaction.user, "roles", ())
    return any(role.id in allowed_roles for role in roles)


class TraceVerificationModal(discord.ui.Modal, title="溯源样本核验"):
    def __init__(self, bot: OdysseiaProtect):
        super().__init__(timeout=900)
        self.bot = bot
        self.file_upload = discord.ui.FileUpload(
            required=True,
            min_values=1,
            max_values=10,
        )
        self.add_item(
            discord.ui.Label(
                text="上传 PNG、ZIP 或 7z",
                description="每个附件独立生成报告；不接受密码包或嵌套压缩包",
                component=self.file_upload,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_trace_admin(interaction):
            await interaction.response.send_message(
                "❌ 您没有使用溯源核验的权限。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            submissions = await self.bot.verification_service.submit(
                interaction=interaction,
                attachments=list(self.file_upload.values),
            )
        except ValueError as exc:
            await interaction.edit_original_response(
                content=f"❌ 无法接收核验任务：{exc}"
            )
            return
        except Exception:
            logger.exception("创建溯源核验任务失败")
            await interaction.edit_original_response(
                content="❌ 创建核验任务时发生内部错误，请稍后重试。"
            )
            return
        lines = [
            f"- `{item.report_id}` · `{item.filename}`" for item in submissions
        ]
        await interaction.edit_original_response(
            content=(
                f"✅ 已接收 {len(submissions)} 个附件并开始处理：\n"
                + "\n".join(lines)
                + "\n\n完成后会立即返回报告；也可使用 `/溯源 报告` 查询。"
            )
        )


class TraceAdminCog(commands.Cog):
    def __init__(self, bot: OdysseiaProtect):
        self.bot = bot

    trace_group = app_commands.Group(name="溯源", description="管理员溯源核验工具。")

    @trace_group.command(name="核验", description="上传 PNG、ZIP 或 7z 样本进行核验。")
    async def verify(self, interaction: discord.Interaction):
        if not is_trace_admin(interaction):
            await interaction.response.send_message(
                "❌ 此命令仅限已配置的溯源管理员在服务器频道中使用。",
                ephemeral=True,
            )
            return
        if not self.bot.traceability_service.available:
            await interaction.response.send_message(
                "❌ 动态溯源密钥未配置，无法核验。", ephemeral=True
            )
            return
        await interaction.response.send_modal(TraceVerificationModal(self.bot))

    @trace_group.command(name="报告", description="查询核验任务状态或获取报告。")
    @app_commands.describe(report_id="核验时返回的 TR-... 任务编号")
    async def report(self, interaction: discord.Interaction, report_id: str):
        if not is_trace_admin(interaction):
            await interaction.response.send_message(
                "❌ 此命令仅限已配置的溯源管理员使用。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        job = await self.bot.verification_service.get_job(report_id)
        if job is None:
            await interaction.followup.send(
                "❌ 找不到该任务；编号可能有误或报告已超过 7 天。",
                ephemeral=True,
            )
            return
        content, report_files = await self.bot.verification_service.report_response(job)
        kwargs = {"content": content, "ephemeral": True}
        if report_files:
            kwargs["files"] = report_files
        await interaction.followup.send(**kwargs)


async def setup(bot: OdysseiaProtect):
    await bot.add_cog(TraceAdminCog(bot))
