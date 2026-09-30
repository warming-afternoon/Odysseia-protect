"""全局协作者授权的数据库访问。"""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import AuthorCollaborator


class CollaboratorRepository:
    """读写按原作者账号保存的全局协作者关系。"""

    async def exists(self, session: AsyncSession, *, author_id: int, user_id: int) -> bool:
        """检查用户是否已获得指定原作者的直接授权。"""
        # 仅查询双方的直接关系，避免将协作者自己的授权传递给其他用户。
        statement = select(AuthorCollaborator.id).where(
            AuthorCollaborator.author_id == author_id,
            AuthorCollaborator.collaborator_id == user_id,
        )
        return (await session.execute(statement)).scalar_one_or_none() is not None

    async def add(self, session: AsyncSession, *, author_id: int, user_id: int,
                  granted_by: int) -> None:
        """新增授权并刷新数据库，由调用方提交事务。"""
        # 保存实际授权操作人，授权关系本身不依赖服务器或帖子记录。
        session.add(AuthorCollaborator(
            author_id=author_id, collaborator_id=user_id, granted_by=granted_by,
        ))
        # 提前触发唯一约束检查，便于服务层处理并发重复授权。
        await session.flush()

    async def remove(self, session: AsyncSession, *, author_id: int, user_id: int) -> bool:
        """删除指定授权并返回是否确实移除了关系。"""
        # 按双方账号直接删除，允许移除已离服或失联的协作者。
        result = await session.execute(delete(AuthorCollaborator).where(
            AuthorCollaborator.author_id == author_id,
            AuthorCollaborator.collaborator_id == user_id,
        ))
        return result.rowcount > 0

    async def page(self, session: AsyncSession, *, author_id: int,
                   page: int = 0) -> tuple[list[int], int]:
        """返回指定原作者的一页协作者账号和名单总人数。"""
        # 单独统计总人数，供界面计算页数和翻页按钮状态。
        count = (await session.execute(
            select(func.count()).select_from(AuthorCollaborator).where(
                AuthorCollaborator.author_id == author_id,
            )
        )).scalar_one()
        # 按授权记录主键稳定排序，每页仅返回协作者身份信息。
        result = await session.execute(
            select(AuthorCollaborator.collaborator_id)
            .where(AuthorCollaborator.author_id == author_id)
            .order_by(AuthorCollaborator.id).offset(page * 20).limit(20)
        )
        return list(result.scalars().all()), count
