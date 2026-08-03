from typing import Optional
from pydantic import BaseModel

from src.database.models import UploadMode
from src.enums import SourceStatus


class ResourceDTO(BaseModel):
    id: int
    filename: Optional[str] = None
    version_info: str = "未提供"
    password: Optional[str] = None
    source_message_id: int
    warehouse_thread_id: Optional[int] = None
    public_thread_id: int
    author_id: Optional[int] = None
    guild_id: Optional[int] = None
    public_thread_name: Optional[str] = None
    source_status: SourceStatus = SourceStatus.UNKNOWN
    upload_mode: Optional[UploadMode] = None
