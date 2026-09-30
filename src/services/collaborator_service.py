"""协作者名单管理；名单查询公开，修改仅限原作者和管理组。"""

import logging

import discord
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.repositories.collaborator import CollaboratorRepository
from src.utils.auth import can_edit_collaborators

logger = logging.getLogger(__name__)


class CollaboratorService:
    """校验授权修改权限并协调事务、操作日志和用户提示。"""

    def __init__(self):
        """初始化协作者授权的数据访问入口。"""
        self.repo = CollaboratorRepository()

    async def change(self, session: AsyncSession, *, interaction: discord.Interaction,
                     author_id: int, user_id: int, adding: bool) -> str:
        """按最新权限添加或移除全局授权，并返回操作结果。"""
        # 先检查名单修改权限，协作者的资源维护权限不能用于授权他人。
        if not can_edit_collaborators(interaction, author_id):
            raise PermissionError("只有原作者本人或管理组才能修改这份协作者名单。")
        # 原作者本身已有维护权限，不创建无意义的自我授权关系。
        if user_id == author_id:
            raise ValueError("原作者已有维护权限，无需给自己授权。")
        if adding:
            # 已有关系直接返回提示，避免重复记录和重复操作日志。
            if await self.repo.exists(session, author_id=author_id, user_id=user_id):
                return "ℹ️ 该用户已经是这位原作者的协作者。"
            try:
                # 使用保存点隔离唯一约束冲突，保留外层事务用于查询确认。
                async with session.begin_nested():
                    await self.repo.add(session, author_id=author_id, user_id=user_id,
                                        granted_by=interaction.user.id)
            except IntegrityError:
                # 并发请求可能抢先完成授权，仅将已存在关系视为重复操作。
                if await self.repo.exists(session, author_id=author_id, user_id=user_id):
                    return "ℹ️ 该用户已经是这位原作者的协作者。"
                raise
        elif not await self.repo.remove(session, author_id=author_id, user_id=user_id):
            # 删除未命中时明确提示，避免把不存在的授权报告为撤销成功。
            return "ℹ️ 该用户不在这位原作者的协作者名单中。"
        # 提交后再记录成功日志，确保日志对应已生效的跨服授权变更。
        await session.commit()
        action = "添加" if adding else "移除"
        logger.info("协作者授权%s: operator=%s author=%s collaborator=%s guild=%s",
                    action, interaction.user.id, author_id, user_id, interaction.guild.id)
        # 在响应中说明授权覆盖现有和未来帖子，提醒操作人跨服影响。
        effect = (
            f"可维护原作者 <@{author_id}> 在所有服务器的现有及未来帖子的资源。"
            if adding else
            f"已取消对原作者 <@{author_id}> 在所有服务器的现有及未来帖子的资源维护权限。"
        )
        return f"✅ 已{action}协作者 <@{user_id}>。\n{effect}"
