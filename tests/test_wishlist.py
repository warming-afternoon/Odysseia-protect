from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.cogs.wishlist_cog import WishlistCog
from src.database.models import UploadMode, WishlistItem
from src.database.repositories.resource import ResourceRepository
from src.database.repositories.thread import ThreadRepository
from src.database.repositories.user import UserRepository
from src.database.repositories.wishlist import WishlistRepository
from src.database.schemas import ResourceCreate, ThreadCreate, UserCreate
from src.dto.resource_dto import ResourceDTO
from src.services.wishlist_service import (
    WishlistPage,
    WishlistPageEntry,
    WishlistService,
)
from src.ui.wishlist_ui import build_wishlist_consent_embed, build_wishlist_render


async def create_resource(
    session: AsyncSession,
    *,
    resource_id_seed: int,
    mode: UploadMode = UploadMode.NORMAL,
):
    thread_repo = ThreadRepository()
    resource_repo = ResourceRepository()
    thread = await thread_repo.create(
        session,
        obj_in=ThreadCreate(
            public_thread_id=10000 + resource_id_seed,
            warehouse_thread_id=20000 + resource_id_seed,
            author_id=999,
        ),
    )
    await session.flush()
    resource = await resource_repo.create(
        session,
        obj_in=ResourceCreate(
            thread_id=thread.id,
            upload_mode=mode,
            filename=f"card-{resource_id_seed}.png",
            version_info=f"v{resource_id_seed}",
            source_message_id=30000 + resource_id_seed,
        ),
    )
    await session.flush()
    return resource


@pytest.mark.asyncio
async def test_wishlist_repository_is_idempotent_and_user_scoped(
    db_session: AsyncSession,
):
    user_repo = UserRepository()
    wishlist_repo = WishlistRepository()
    resource = await create_resource(db_session, resource_id_seed=1)
    for user_id in (101, 202):
        await user_repo.create(
            db_session,
            obj_in=UserCreate(
                id=user_id,
                has_agreed_to_privacy_policy=True,
                has_agreed_to_wishlist_policy=True,
            ),
        )
    await db_session.flush()

    assert await wishlist_repo.add_idempotent(
        db_session, user_id=101, resource_id=resource.id
    )
    assert not await wishlist_repo.add_idempotent(
        db_session, user_id=101, resource_id=resource.id
    )
    assert await wishlist_repo.add_idempotent(
        db_session, user_id=202, resource_id=resource.id
    )
    await db_session.flush()

    assert await wishlist_repo.count_for_user(db_session, user_id=101) == 1
    assert await wishlist_repo.count_for_user(db_session, user_id=202) == 1
    assert await wishlist_repo.remove_for_user(
        db_session, user_id=101, resource_id=resource.id
    )
    assert not await wishlist_repo.is_wishlisted(
        db_session, user_id=101, resource_id=resource.id
    )
    assert await wishlist_repo.is_wishlisted(
        db_session, user_id=202, resource_id=resource.id
    )


@pytest.mark.asyncio
async def test_wishlist_repository_orders_newest_and_paginates_eight(
    db_session: AsyncSession,
):
    user_repo = UserRepository()
    wishlist_repo = WishlistRepository()
    await user_repo.create(
        db_session,
        obj_in=UserCreate(
            id=101,
            has_agreed_to_privacy_policy=True,
            has_agreed_to_wishlist_policy=True,
        ),
    )
    resources = [
        await create_resource(db_session, resource_id_seed=index)
        for index in range(1, 11)
    ]
    await db_session.flush()
    base_time = datetime(2026, 1, 1)
    for index, resource in enumerate(resources):
        db_session.add(
            WishlistItem(
                user_id=101,
                resource_id=resource.id,
                created_at=base_time + timedelta(minutes=index),
            )
        )
    await db_session.flush()

    first_page = await wishlist_repo.get_page_for_user(
        db_session, user_id=101, offset=0, limit=8
    )
    second_page = await wishlist_repo.get_page_for_user(
        db_session, user_id=101, offset=8, limit=8
    )

    assert [item.resource_id for item in first_page] == [
        resource.id for resource in reversed(resources[2:])
    ]
    assert [item.resource_id for item in second_page] == [
        resources[1].id,
        resources[0].id,
    ]


@pytest.mark.asyncio
async def test_deleting_resource_cascades_wishlist_item(db_session: AsyncSession):
    user_repo = UserRepository()
    wishlist_repo = WishlistRepository()
    resource_repo = ResourceRepository()
    await user_repo.create(
        db_session,
        obj_in=UserCreate(
            id=101,
            has_agreed_to_privacy_policy=True,
            has_agreed_to_wishlist_policy=True,
        ),
    )
    resource = await create_resource(db_session, resource_id_seed=1)
    await db_session.flush()
    await wishlist_repo.add_idempotent(
        db_session,
        user_id=101,
        resource_id=resource.id,
    )
    await db_session.flush()

    await resource_repo.remove(db_session, id=resource.id)
    await db_session.flush()

    remaining = await db_session.execute(select(WishlistItem))
    assert remaining.scalars().all() == []


@pytest.mark.asyncio
async def test_wishlist_service_requires_consent_then_adds(db_session: AsyncSession):
    resource = await create_resource(db_session, resource_id_seed=1)
    service = WishlistService(
        MagicMock(),
        ResourceRepository(),
        ThreadRepository(),
        UserRepository(),
        WishlistRepository(),
    )

    assert (
        await service.add(
            db_session,
            user_id=123,
            resource_id=resource.id,
        )
        == "needs_consent"
    )
    assert (
        await service.accept_policy_and_add(
            db_session,
            user_id=123,
            resource_id=resource.id,
        )
        == "added"
    )
    await db_session.flush()
    user = await UserRepository().get(db_session, id=123)
    assert user is not None
    assert user.has_agreed_to_privacy_policy is False
    assert user.has_agreed_to_wishlist_policy is True
    assert (
        await service.add(
            db_session,
            user_id=123,
            resource_id=resource.id,
        )
        == "already_added"
    )


@pytest.mark.asyncio
async def test_wishlist_consent_does_not_grant_upload_consent(
    db_session: AsyncSession,
):
    resource = await create_resource(db_session, resource_id_seed=2)
    user_repo = UserRepository()
    await user_repo.create(
        db_session,
        obj_in=UserCreate(
            id=456,
            has_agreed_to_privacy_policy=False,
            has_agreed_to_wishlist_policy=False,
        ),
    )
    service = WishlistService(
        MagicMock(),
        ResourceRepository(),
        ThreadRepository(),
        user_repo,
        WishlistRepository(),
    )

    assert await service.accept_policy_and_add(
        db_session,
        user_id=456,
        resource_id=resource.id,
    ) == "added"

    user = await user_repo.get(db_session, id=456)
    assert user is not None
    assert user.has_agreed_to_privacy_policy is False
    assert user.has_agreed_to_wishlist_policy is True


@pytest.mark.asyncio
async def test_wishlist_service_marks_only_failed_url_unavailable(
    db_session: AsyncSession,
):
    user_repo = UserRepository()
    wishlist_repo = WishlistRepository()
    await user_repo.create(
        db_session,
        obj_in=UserCreate(
            id=123,
            has_agreed_to_privacy_policy=True,
            has_agreed_to_wishlist_policy=True,
        ),
    )
    first = await create_resource(db_session, resource_id_seed=1)
    second = await create_resource(db_session, resource_id_seed=2)
    await db_session.flush()
    await wishlist_repo.add_idempotent(
        db_session, user_id=123, resource_id=first.id
    )
    await wishlist_repo.add_idempotent(
        db_session, user_id=123, resource_id=second.id
    )
    await db_session.flush()

    bot = MagicMock()
    bot.download_service.fetch_fresh_url = AsyncMock(
        side_effect=["https://example.com/second.png", RuntimeError("gone")]
    )
    service = WishlistService(
        bot,
        ResourceRepository(),
        ThreadRepository(),
        user_repo,
        wishlist_repo,
    )
    page = await service.get_page(db_session, user_id=123, page=1)

    assert page.total == 2
    assert len(page.entries) == 2
    assert sum(entry.url is not None for entry in page.entries) == 1
    assert sum(entry.error is not None for entry in page.entries) == 1
    bot.dispatch.assert_not_called()


def make_page_entry(index: int, url: str | None) -> WishlistPageEntry:
    return WishlistPageEntry(
        item_id=index,
        resource=ResourceDTO(
            id=index,
            filename=f"card-{index}.png",
            version_info=f"v{index}",
            source_message_id=100 + index,
            public_thread_id=200 + index,
            upload_mode=UploadMode.NORMAL,
        ),
        created_at=datetime(2026, 1, 1),
        url=url,
        error=None if url else "gone",
    )


def test_wishlist_consent_uses_audience_specific_policy():
    embed = build_wishlist_consent_embed()

    assert embed.title == "❤️ 请阅读并同意心愿单数据存储声明"
    assert "收藏者" in embed.description
    assert "资源版本 ID" in embed.description
    assert "受保护文件" not in embed.description


@pytest.mark.asyncio
async def test_wishlist_v2_page_uses_exact_component_budget_and_copy_block():
    entries = [
        make_page_entry(index, f"https://example.com/{index}.png")
        for index in range(1, 9)
    ]
    render = build_wishlist_render(
        service=MagicMock(),
        user_id=123,
        page_data=WishlistPage(entries=entries, page=1, max_page=2, total=10),
    )

    assert render.file is None
    assert render.view.total_children_count == 40
    assert render.view.content_length() <= 4000
    text = "\n".join(
        item.content
        for item in render.view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert "https://example.com/1.png" in text
    assert "```\n" in text
    assert "1/2" in [
        item.label
        for item in render.view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]


@pytest.mark.asyncio
async def test_wishlist_v2_overflow_falls_back_to_txt():
    long_url = "https://example.com/" + ("x" * 490)
    entries = [
        WishlistPageEntry(
            item_id=index,
            resource=ResourceDTO(
                id=index,
                filename="f" * 255,
                version_info="v" * 100,
                source_message_id=index,
                public_thread_id=index,
                upload_mode=UploadMode.NORMAL,
            ),
            created_at=datetime(2026, 1, 1),
            url=long_url,
        )
        for index in range(1, 9)
    ]
    render = build_wishlist_render(
        service=MagicMock(),
        user_id=123,
        page_data=WishlistPage(entries=entries, page=2, max_page=3, total=24),
    )

    assert render.file is not None
    assert render.file.filename == "wishlist-page-2-urls.txt"
    assert render.view.total_children_count == 40
    assert render.view.content_length() <= 4000
    assert any(
        isinstance(item, discord.ui.File)
        for item in render.view.walk_children()
    )


def test_wishlist_command_is_registered_with_expected_name():
    cog = WishlistCog(MagicMock())
    commands = {command.name for command in cog.__cog_app_commands__}
    assert "心愿单" in commands
