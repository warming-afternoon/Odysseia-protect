from pydantic import BaseModel


class WishlistItemUpdate(BaseModel):
    """心愿单关联的空更新参数，保留现有仓储接口。"""

    pass
