from typing import Any, Generic, Sequence, Type, TypeVar, Union
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import Base

# --- 类型变量定义 ---
# 'ModelType' 用于表示我们的 SQLAlchemy 模型（例如 Thread, Resource）
# 使用字符串前向引用以避免循环导入，并忽略类型检查器的误报
ModelType = TypeVar("ModelType", bound="Base")  # type: ignore
# 'CreateSchemaType' 和 'UpdateSchemaType' 用于表示 Pydantic 模型
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class BaseRepository(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """
    包含通用 CRUD 操作的基础仓库。

    所有其他的仓库都将继承这个类，从而自动获得基础的数据操作方法。
    """

    def __init__(self, model: Type[ModelType]):
        """
        初始化仓库。

        :param model: 与此仓库关联的 SQLAlchemy 模型类。
        """
        self.model = model

    async def create(
        self, session: AsyncSession, *, obj_in: CreateSchemaType
    ) -> ModelType:
        """
        创建一个新的记录。

        :param session: 数据库会话。
        :param obj_in: Pydantic 模型，包含新记录的数据。
        :return: 新创建的 ORM 对象。
        """
        # 使用 Pydantic v2 的 model_dump 方法将模型转换为字典
        obj_in_data = obj_in.model_dump()
        db_obj = self.model(**obj_in_data)
        session.add(db_obj)
        # commit 和 refresh 将由服务层通过 Unit of Work 模式管理
        # await session.commit()
        # await session.refresh(db_obj)
        return db_obj

    async def get(self, session: AsyncSession, id: Any) -> ModelType | None:
        """
        根据 ID 获取单个记录。

        :param session: 数据库会话。
        :param id: 记录的主键 ID。
        :return: 找到的 ORM 对象，如果不存在则返回 None。
        """
        statement = select(self.model).where(self.model.id == id)
        result = await session.execute(statement)
        return result.scalar_one_or_none()

    async def get_multi(
        self, session: AsyncSession, **kwargs: Any
    ) -> Sequence[ModelType]:
        """
        根据任意数量的字段获取多个记录。

        :param session: 数据库会话。
        :param kwargs: 用于过滤的字段和值。
        :return: 找到的 ORM 对象列表。
        """
        statement = select(self.model)
        for key, value in kwargs.items():
            statement = statement.where(getattr(self.model, key) == value)
        result = await session.execute(statement)
        return result.scalars().all()

    async def get_all(
        self, session: AsyncSession, *, skip: int = 0, limit: int = 100
    ) -> Sequence[ModelType]:
        """
        获取所有记录，支持分页。

        :param session: 数据库会话。
        :param skip: 跳过的记录数。
        :param limit: 返回的最大记录数。
        :return: ORM 对象列表。
        """
        statement = select(self.model).offset(skip).limit(limit)
        result = await session.execute(statement)
        return result.scalars().all()

    async def update(
        self,
        session: AsyncSession,
        *,
        db_obj: ModelType,
        obj_in: Union[UpdateSchemaType, dict[str, Any]],
    ) -> ModelType:
        """
        更新一个已存在的记录。

        :param session: 数据库会话。
        :param db_obj: 要更新的 ORM 对象。
        :param obj_in: Pydantic 模型或包含更新数据的字典。
        :return: 更新后的 ORM 对象。
        """
        update_data: dict[str, Any]
        if isinstance(obj_in, BaseModel):
            # 使用 exclude_unset=True 确保我们只更新被明确设置的字段
            update_data = obj_in.model_dump(exclude_unset=True)
        else:
            update_data = obj_in

        for field, value in update_data.items():
            setattr(db_obj, field, value)

        session.add(db_obj)
        # commit 和 refresh 将由服务层管理
        # await session.commit()
        # await session.refresh(db_obj)
        return db_obj

    async def remove(self, session: AsyncSession, *, id: int) -> ModelType | None:
        """
        根据 ID 删除一个记录。

        :param session: 数据库会话。
        :param id: 要删除的记录的主键 ID。
        :return: 被删除的 ORM 对象，如果不存在则返回 None。
        """
        obj = await self.get(session, id)
        if obj:
            await session.delete(obj)
            # commit 应由服务层管理
            # await session.commit() # This was incorrect.
        return obj
