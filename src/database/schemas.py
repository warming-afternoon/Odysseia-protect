from typing import Optional

from pydantic import BaseModel

from .models import UploadMode

# ==================================
# Thread Schemas
# ==================================


class ThreadBase(BaseModel):
    public_thread_id: int
    author_id: int
    warehouse_thread_id: Optional[int] = None
    reaction_required: bool = False
    reaction_emoji: Optional[str] = None


class ThreadCreate(ThreadBase):
    pass


class ThreadUpdate(BaseModel):
    warehouse_thread_id: Optional[int] = None
    reaction_required: Optional[bool] = None
    reaction_emoji: Optional[str] = None


class ThreadInDB(ThreadBase):
    id: int

    class Config:
        from_attributes = True


# ==================================
# User Schemas
# ==================================


class UserBase(BaseModel):
    id: int
    has_agreed_to_privacy_policy: bool = False


class UserCreate(UserBase):
    pass


class UserUpdate(BaseModel):
    has_agreed_to_privacy_policy: bool


class UserInDB(UserBase):
    class Config:
        from_attributes = True


# ==================================
# Resource Schemas
# ==================================


class ResourceBase(BaseModel):
    thread_id: int
    upload_mode: UploadMode
    filename: str
    version_info: str
    source_message_id: int
    description: Optional[str] = None
    password: Optional[str] = None


class ResourceCreate(ResourceBase):
    pass


class ResourceUpdate(BaseModel):
    version_info: Optional[str] = None
    description: Optional[str] = None
    password: Optional[str] = None


class ResourceInDB(ResourceBase):
    id: int

    class Config:
        from_attributes = True
