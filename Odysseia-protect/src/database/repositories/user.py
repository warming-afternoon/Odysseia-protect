from ..models import User
from ..schemas import UserCreate, UserUpdate
from .base import BaseRepository


class UserRepository(BaseRepository[User, UserCreate, UserUpdate]):
    """
    User 模型的数据库操作仓库。
    """

    def __init__(self):
        super().__init__(model=User)
