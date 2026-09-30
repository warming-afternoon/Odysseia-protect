"""统一导出并注册所有 ORM 模型。"""

from .base import Base
from .author_collaborator import AuthorCollaborator
from .thread import Thread
from .resource import Resource
from .user import User
from .wishlist_item import WishlistItem
from .trace_verification_job import TraceVerificationJob

__all__ = [
    "Base",
    "AuthorCollaborator",
    "Thread",
    "Resource",
    "User",
    "WishlistItem",
    "TraceVerificationJob",
]
