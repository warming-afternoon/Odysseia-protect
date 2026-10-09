from src.models import User
from src.qo.user_create import UserCreate
from src.qo.user_update import UserUpdate

from .base import BaseRepository


class UserRepository(BaseRepository[User, UserCreate, UserUpdate]):
    """
    User 模型的数据库操作仓库。
    """

    def __init__(self):
        super().__init__(model=User)
