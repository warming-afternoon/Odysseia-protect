from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
from src.enums import SourceStatus
from src.services.wishlist_service import (
    WISHLIST_PAGE_SIZE,
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
            guild_id=80000 + resource_id_seed,
            public_thread_name=f"来源帖子 {resource_id_seed}",
            source_status=SourceStatus.ACTIVE,
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
async def test_wishlist_repository_bulk_remove_is_item_and_user_scoped(
    db_session: AsyncSession,
):
    user_repo = UserRepository()
    wishlist_repo = WishlistRepository()
    resources = [
        await create_resource(db_session, resource_id_seed=index)
        for index in range(11, 13)
    ]
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
    for resource in resources:
        await wishlist_repo.add_idempotent(
            db_session,
            user_id=101,
            resource_id=resource.id,
        )
    await wishlist_repo.add_idempotent(
        db_session,
        user_id=202,
        resource_id=resources[0].id,
    )
    await db_session.flush()

    user_items = await wishlist_repo.get_page_for_user(
        db_session,
        user_id=101,
        offset=0,
        limit=10,
    )
    other_items = await wishlist_repo.get_page_for_user(
        db_session,
        user_id=202,
        offset=0,
        limit=10,
    )
    removed = await wishlist_repo.remove_items_for_user(
        db_session,
        user_id=101,
        item_ids=(user_items[0].id, other_items[0].id, user_items[0].id),
    )

    assert removed == 1
    assert await wishlist_repo.count_for_user(db_session, user_id=101) == 1
    assert await wishlist_repo.count_for_user(db_session, user_id=202) == 1


@pytest.mark.asyncio
async def test_wishlist_repository_orders_newest_and_paginates_six(
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
        db_session, user_id=101, offset=0, limit=6
    )
    second_page = await wishlist_repo.get_page_for_user(
        db_session, user_id=101, offset=6, limit=6
    )

    assert [item.resource_id for item in first_page] == [
        resource.id for resource in reversed(resources[4:])
    ]
    assert [item.resource_id for item in second_page] == [
        resource.id for resource in reversed(resources[:4])
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


@pytest.mark.asyncio
async def test_wishlist_service_uses_six_items_and_clamps_after_last_page_delete(
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
    resources = [
        await create_resource(db_session, resource_id_seed=index)
        for index in range(21, 28)
    ]
    await db_session.flush()
    for resource in resources:
        await wishlist_repo.add_idempotent(
            db_session,
            user_id=123,
            resource_id=resource.id,
        )
    await db_session.flush()

    bot = MagicMock()
    bot.download_service.fetch_fresh_url = AsyncMock(
        return_value="https://example.com/card.png"
    )
    service = WishlistService(
        bot,
        ResourceRepository(),
        ThreadRepository(),
        user_repo,
        wishlist_repo,
    )
    last_page = await service.get_page(db_session, user_id=123, page=2)

    assert WISHLIST_PAGE_SIZE == 6
    assert last_page.page == 2
    assert len(last_page.entries) == 1

    await service.remove_items(
        db_session,
        user_id=123,
        item_ids=(last_page.entries[0].item_id,),
    )
    refreshed = await service.get_page(db_session, user_id=123, page=2)

    assert refreshed.page == 1
    assert refreshed.max_page == 1
    assert refreshed.total == 6
    assert len(refreshed.entries) == 6

    remaining_ids = tuple(entry.item_id for entry in refreshed.entries)
    assert await service.remove_items(
        db_session,
        user_id=123,
        item_ids=remaining_ids,
    ) == 6
    assert await service.remove_items(
        db_session,
        user_id=123,
        item_ids=remaining_ids,
    ) == 0
    empty_page = await service.get_page(db_session, user_id=123, page=1)

    assert empty_page.page == 1
    assert empty_page.max_page == 1
    assert empty_page.total == 0
    assert empty_page.entries == []


def make_page_entry(index: int, url: str | None) -> WishlistPageEntry:
    return WishlistPageEntry(
        item_id=index,
        resource=ResourceDTO(
            id=index,
            filename=f"card-{index}.png",
            version_info=f"v{index}",
            source_message_id=100 + index,
            public_thread_id=200 + index,
            author_id=400 + index,
            guild_id=300,
            public_thread_name=f"来源帖子 {index}",
            source_status=SourceStatus.ACTIVE,
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
async def test_deleted_source_keeps_clickable_title_and_warning():
    entry = make_page_entry(1, "https://example.com/1.png")
    entry.resource.source_status = SourceStatus.DELETED
    render = build_wishlist_render(
        service=MagicMock(),
        user_id=123,
        page_data=WishlistPage(entries=[entry], page=1, max_page=1, total=1),
    )
    text = "\n".join(
        item.content
        for item in render.view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )

    assert "[来源帖子 1](https://discord.com/channels/300/201)" in text
    assert (
        "### [来源帖子 1](https://discord.com/channels/300/201)\n"
        "⚠️ **原帖已删除**\n"
        "**作者：** <@401>"
    ) in text


@pytest.mark.asyncio
async def test_wishlist_v2_page_uses_exact_component_budget_and_copy_block():
    entries = [
        make_page_entry(index, f"https://example.com/{index}.png")
        for index in range(1, 7)
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
    assert "### [来源帖子 1](https://discord.com/channels/300/201)" in text
    assert "**作者：** <@401>" in text
    assert "角色卡下载" not in text
    # 六个单项 URL 和一个本页汇总代码块都应支持 Discord 一键复制。
    for index in range(1, 7):
        assert f"```\nhttps://example.com/{index}.png\n```" in text
    assert text.count("```") == 14
    button_labels = [
        item.label
        for item in render.view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]
    assert "1/2" in button_labels
    assert button_labels.count("移除") == 6
    assert "移除本页全部项" in button_labels


@pytest.mark.asyncio
async def test_wishlist_cards_keep_remove_enabled_for_all_download_states():
    entries = [
        make_page_entry(1, "https://example.com/normal.png"),
        make_page_entry(2, "https://example.com/" + ("x" * 513)),
        make_page_entry(3, None),
    ]
    render = build_wishlist_render(
        service=MagicMock(),
        user_id=123,
        page_data=WishlistPage(entries=entries, page=1, max_page=1, total=3),
    )
    buttons = [
        item
        for item in render.view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]

    remove_buttons = [button for button in buttons if button.label == "移除"]
    assert len(remove_buttons) == 3
    assert all(not button.disabled for button in remove_buttons)
    assert next(button for button in buttons if button.label == "打开下载链接")
    assert next(button for button in buttons if button.label == "URL 见导出内容").disabled
    assert next(button for button in buttons if button.label == "资源不可用").disabled


@pytest.mark.asyncio
async def test_remove_page_button_uses_rendered_item_id_snapshot():
    service = MagicMock()
    service.remove_items = AsyncMock(return_value=2)
    replacement_view = MagicMock()
    service.build_render = AsyncMock(
        return_value=SimpleNamespace(view=replacement_view, file=None)
    )
    entries = [
        make_page_entry(11, "https://example.com/11.png"),
        make_page_entry(12, "https://example.com/12.png"),
    ]
    render = build_wishlist_render(
        service=service,
        user_id=123,
        page_data=WishlistPage(entries=entries, page=2, max_page=3, total=14),
    )
    remove_page_button = next(
        item
        for item in render.view.walk_children()
        if isinstance(item, discord.ui.Button)
        and item.label == "移除本页全部项"
    )
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )

    with patch(
        "src.ui.wishlist_ui.AsyncSessionLocal",
        return_value=session_context,
    ):
        await remove_page_button.callback(interaction)

    service.remove_items.assert_awaited_once_with(
        session,
        user_id=123,
        item_ids=(11, 12),
    )
    service.build_render.assert_awaited_once_with(
        session,
        user_id=123,
        page=2,
    )
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    interaction.edit_original_response.assert_awaited_once_with(
        view=replacement_view,
        attachments=[],
    )


@pytest.mark.asyncio
async def test_single_remove_rolls_back_and_keeps_panel_on_database_error():
    service = MagicMock()
    service.remove = AsyncMock(side_effect=RuntimeError("database unavailable"))
    service.build_render = AsyncMock()
    render = build_wishlist_render(
        service=service,
        user_id=123,
        page_data=WishlistPage(
            entries=[make_page_entry(7, "https://example.com/7.png")],
            page=1,
            max_page=1,
            total=1,
        ),
    )
    remove_button = next(
        item
        for item in render.view.walk_children()
        if isinstance(item, discord.ui.Button) and item.label == "移除"
    )
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )

    with patch(
        "src.ui.wishlist_ui.AsyncSessionLocal",
        return_value=session_context,
    ):
        await remove_button.callback(interaction)

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    service.build_render.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_awaited_once_with(
        "❌ 移除心愿单项目时发生内部错误，请稍后重试。",
        ephemeral=True,
    )


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
        for index in range(1, 7)
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
