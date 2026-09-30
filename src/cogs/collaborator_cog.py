"""跨服协作者授权命令。"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.database.database import AsyncSessionLocal
from src.services.collaborator_service import CollaboratorService
from src.utils.auth import parse_user_id, send_private_error

logger = logging.getLogger(__name__)


class CollaboratorListView(discord.ui.View):
    """将协作者名单分页展示给发起查询的用户。"""

    def __init__(self, service: CollaboratorService, author_id: int, viewer_id: int):
        """初始化查询目标、面板操作人和当前页。"""
        super().__init__(timeout=300)
        self.service = service
        self.author_id = author_id
        self.viewer_id = viewer_id
        self.page = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """仅允许名单查询的发起人操作此分页面板。"""
        # 其他用户可以自行查询名单，但不能接管已有面板的翻页操作。
        if interaction.user.id != self.viewer_id:
            await send_private_error(interaction, "请自行使用 /协作者 列表 打开名单。")
            return False
        return True

    async def render(self) -> discord.Embed:
        """读取最新名单并生成当前页及翻页按钮状态。"""
        # 每次渲染重新查询，确保名单变化后页面及时更新。
        async with AsyncSessionLocal() as session:
            users, total = await self.service.repo.page(
                session, author_id=self.author_id, page=self.page,
            )
            pages = max(1, (total + 19) // 20)
            # 末页记录被移除后，退回仍存在的最后一页重新读取。
            if self.page >= pages:
                self.page = pages - 1
                users, total = await self.service.repo.page(
                    session, author_id=self.author_id, page=self.page,
                )
        # 空名单也保留一页展示，并禁用超出页边界的按钮。
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page >= pages - 1
        # 展示协作者身份和跨服生效提示，不公开授权操作日志。
        embed = discord.Embed(
            title="🤝 协作者名单",
            description=f"原作者：<@{self.author_id}>\n\n" + (
                "\n".join(f"<@{user_id}> · `{user_id}`" for user_id in users)
                if users else "暂无协作者。"
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"第 {self.page + 1}/{pages} 页 · 共 {total} 人 · 授权跨服生效")
        return embed

    @discord.ui.button(label="上一页", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        """切换到上一页并更新原私密消息。"""
        # 先确认交互，再查询和编辑，避免数据库查询耗尽响应时限。
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        await interaction.edit_original_response(embed=await self.render(), view=self)

    @discord.ui.button(label="下一页", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """切换到下一页并通过渲染校正最新页边界。"""
        # 页边界由最新名单决定，渲染时会处理翻页期间的名单缩减。
        await interaction.response.defer()
        self.page += 1
        await interaction.edit_original_response(embed=await self.render(), view=self)


class CollaboratorCog(commands.Cog):
    """提供服务器内的协作者授权修改和名单查询命令。"""

    collaborator_group = app_commands.Group(
        name="协作者", description="管理按原作者授权、跨服生效的协作者名单。", guild_only=True,
    )

    def __init__(self, bot):
        """保存机器人实例并初始化协作者服务。"""
        self.bot = bot
        self.service = CollaboratorService()

    async def _change(self, interaction, *, user: str, author: str | None, adding: bool):
        """统一处理授权增删命令的参数、事务和私密响应。"""
        # 即使命令已限制服务器使用，仍在执行入口拒绝私聊调用。
        if interaction.guild is None:
            await send_private_error(interaction, "此命令只能在服务器内使用。")
            return
        # 数据库操作前确认交互，后续结果和错误保持私密。
        await interaction.response.defer(ephemeral=True)
        try:
            # 未指定原作者时管理自己的名单，字符串参数兼容失联账号。
            author_id = interaction.user.id if author is None else parse_user_id(author)
            user_id = parse_user_id(user)
            # 服务层负责权限复核、提交授权变更和记录实际操作人。
            async with AsyncSessionLocal() as session:
                message = await self.service.change(
                    session, interaction=interaction, author_id=author_id,
                    user_id=user_id, adding=adding,
                )
            # 仅显示账号身份，不因响应中的提及触发通知。
            await interaction.followup.send(
                message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
        except (ValueError, PermissionError) as exc:
            # 参数和权限错误直接给出可理解的原因。
            await send_private_error(interaction, str(exc))
        except Exception:
            # 内部异常记录到日志，对用户提供统一的重试提示。
            logger.exception("修改协作者授权失败")
            await send_private_error(interaction, "修改授权失败，请稍后重试。")

    @collaborator_group.command(name="添加", description="添加协作者，授权覆盖原作者的所有服务器帖子。")
    @app_commands.rename(user="用户", author="原作者id")
    @app_commands.describe(user="用户提及或数字 ID", author="默认自己；指定他人仅限管理组")
    async def add(self, interaction: discord.Interaction, user: str, author: str | None = None):
        """为自己或管理组指定的原作者添加协作者。"""
        await self._change(interaction, user=user, author=author, adding=True)

    @collaborator_group.command(name="移除", description="移除协作者，撤销跨服资源维护权限。")
    @app_commands.rename(user="用户", author="原作者id")
    @app_commands.describe(user="用户提及或数字 ID，离服账号也可移除", author="默认自己；指定他人仅限管理组")
    async def remove(self, interaction: discord.Interaction, user: str, author: str | None = None):
        """移除指定协作者并撤销其跨服资源维护权限。"""
        await self._change(interaction, user=user, author=author, adding=False)

    @collaborator_group.command(name="列表", description="查看任意原作者的协作者名单，每页 20 人。")
    @app_commands.rename(author="原作者id")
    @app_commands.describe(author="默认自己；也可指定其他原作者的 ID")
    async def list_collaborators(self, interaction: discord.Interaction, author: str | None = None):
        """允许任意服务器用户私密查看任意原作者的协作者名单。"""
        # 查询无需管理权限，但与其他协作者命令一样禁止在私聊使用。
        if interaction.guild is None:
            await send_private_error(interaction, "此命令只能在服务器内使用。")
            return
        await interaction.response.defer(ephemeral=True)
        try:
            # 未指定原作者时查询自己，并将分页面板绑定给当前查看者。
            author_id = interaction.user.id if author is None else parse_user_id(author)
            view = CollaboratorListView(self.service, author_id, interaction.user.id)
            # 名单仅返回给发起人，账号提及不发送通知。
            await interaction.followup.send(
                embed=await view.render(), view=view, ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except ValueError as exc:
            # 非法原作者账号返回输入提示，避免进入数据库查询。
            await send_private_error(interaction, str(exc))
        except Exception:
            # 查询异常保留日志，避免向用户暴露内部错误细节。
            logger.exception("查询协作者名单失败")
            await send_private_error(interaction, "查询名单失败，请稍后重试。")


async def setup(bot):
    """加载协作者命令模块并注册到机器人。"""
    await bot.add_cog(CollaboratorCog(bot))
