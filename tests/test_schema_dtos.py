"""历史数据库 schema 拆分后的数据契约验收。"""

from types import SimpleNamespace

import pytest

from src.dto.resource_in_db import ResourceInDB
from src.dto.thread_in_db import ThreadInDB
from src.dto.user_in_db import UserInDB
from src.dto.wishlist_item_in_db import WishlistItemInDB
from src.enums import SourceStatus, UploadMode
from src.qo.resource_create import ResourceCreate
from src.qo.resource_update import ResourceUpdate
from src.qo.thread_create import ThreadCreate
from src.qo.user_create import UserCreate


def test_creation_defaults_and_partial_update_semantics():
    """迁移目录不改变默认值和显式置空语义。"""
    thread = ThreadCreate(public_thread_id=1, author_id=2)
    assert thread.source_status == SourceStatus.UNKNOWN and thread.guild_id is None
    user = UserCreate(id=2)
    assert (
        not user.has_agreed_to_privacy_policy and not user.has_agreed_to_wishlist_policy
    )
    resource = ResourceCreate(
        thread_id=1,
        upload_mode=UploadMode.SECURE,
        filename="a.png",
        version_info="v1",
        source_message_id=3,
    )
    assert not resource.trace_enabled and resource.password is None
    assert ResourceUpdate().model_dump(exclude_unset=True) == {}
    assert ResourceUpdate(password=None).model_dump(exclude_unset=True) == {
        "password": None
    }


@pytest.mark.parametrize(
    "model, attributes",
    [
        (ThreadInDB, {"id": 10, "public_thread_id": 20, "author_id": 30}),
        (UserInDB, {"id": 30}),
        (
            ResourceInDB,
            {
                "id": 40,
                "thread_id": 10,
                "upload_mode": UploadMode.SECURE,
                "filename": "a.png",
                "version_info": "v1",
                "source_message_id": 50,
            },
        ),
        (WishlistItemInDB, {"id": 60, "user_id": 30, "resource_id": 40}),
    ],
)
def test_read_dtos_can_detach_from_attribute_objects(model, attributes):
    """返回 DTO 可从 ORM 风格属性构建，字段值保持不变。"""
    data = model.model_validate(SimpleNamespace(**attributes))
    for field, value in attributes.items():
        assert getattr(data, field) == value
    assert model.__module__.startswith("src.dto.")
