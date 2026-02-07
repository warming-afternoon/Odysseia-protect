from typing import Optional
from pydantic import BaseModel


class ResourceDTO(BaseModel):
    id: int
    password: Optional[str] = None
    source_message_id: int
    warehouse_thread_id: Optional[int] = None
    public_thread_id: int