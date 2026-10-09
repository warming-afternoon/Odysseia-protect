"""由独立心愿单 DTO 构建展示组件和导出附件。"""

import io
from typing import TYPE_CHECKING

import discord

from src.config import WISHLIST_POLICY_TEXT
from src.dto.wishlist_page import WishlistPage
from src.dto.wishlist_render import WishlistRender
from src.enums import SourceStatus
from src.ui.wishlist_page_jump_modal import WishlistPageJumpModal
from src.ui.wishlist_view import WishlistView

if TYPE_CHECKING:
    from src.services.wishlist_service import WishlistService


def _resource_card(
    entry,
    *,
    view: WishlistView,
    include_raw_url: bool,
) -> discord.ui.Container:
    """由单个资源快照构建收藏项卡片。"""
    # 由单个资源快照构建收藏项卡片。
    resource = entry.resource
    version = discord.utils.escape_markdown(resource.version_info or "未命名")
    filename = discord.utils.escape_markdown(resource.filename or "未命名文件")
    source_name = resource.public_thread_name or (
        f"未知帖子（ID: {resource.public_thread_id}）"
    )
    source_label = discord.utils.escape_markdown(source_name).replace("]", "\\]")
    if resource.public_thread_name and resource.guild_id:
        source_url = (
            "https://discord.com/channels/"
            f"{resource.guild_id}/{resource.public_thread_id}"
        )
        source_heading = f"### [{source_label}]({source_url})"
    else:
        source_heading = f"### {source_label}"

    lines = [source_heading]
    if resource.source_status == SourceStatus.DELETED:
        lines.append("⚠️ **原帖已删除**")
    author = f"<@{resource.author_id}>" if resource.author_id else "未知作者"
    lines.extend(
        [
            f"**作者：** {author}",
            f"**版本：** {version}",
            f"**文件：** `{filename}`",
        ]
    )
    if entry.url:
        if include_raw_url:
            lines.extend(
                [
                    "",
                    "📋 **SillyTavern 快速导入 URL**",
                    f"```\n{entry.url}\n```",
                ]
            )
        if len(entry.url) <= 512:
            download_button = discord.ui.Button(
                label="打开下载链接",
                style=discord.ButtonStyle.link,
                url=entry.url,
            )
        else:
            download_button = discord.ui.Button(
                label="URL 见导出内容",
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
    else:
        lines.extend(["", "⚠️ **资源不可用：** 源消息或附件已失效。"])
        download_button = discord.ui.Button(
            label="资源不可用",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )

    remove_button = discord.ui.Button(
        label="移除",
        style=discord.ButtonStyle.danger,
    )

    async def remove_callback(interaction: discord.Interaction):
        """按当前页面快照执行移除操作。"""
        # 按当前页面快照执行移除操作。
        await view._remove_resource(interaction, resource.id)

    remove_button.callback = remove_callback
    return discord.ui.Container(
        discord.ui.TextDisplay("\n".join(lines)),
        discord.ui.ActionRow(download_button, remove_button),
        accent_color=discord.Color.green(),
    )


def _pagination_row(
    view: WishlistView,
) -> discord.ui.ActionRow:
    """根据当前页生成导航和跳页按钮。"""
    # 根据当前页生成导航和跳页按钮。
    first = discord.ui.Button(
        label="⏮️",
        style=discord.ButtonStyle.secondary,
        disabled=view.page == 1,
    )
    previous = discord.ui.Button(
        label="◀️",
        style=discord.ButtonStyle.secondary,
        disabled=view.page == 1,
    )
    current = discord.ui.Button(
        label=f"{view.page}/{view.max_page}",
        style=discord.ButtonStyle.primary,
    )
    next_page = discord.ui.Button(
        label="▶️",
        style=discord.ButtonStyle.secondary,
        disabled=view.page == view.max_page,
    )
    last = discord.ui.Button(
        label="⏭️",
        style=discord.ButtonStyle.secondary,
        disabled=view.page == view.max_page,
    )

    async def first_callback(interaction: discord.Interaction):
        """打开心愿单第一页。"""
        # 打开心愿单第一页。
        await view._go_to_page(interaction, 1)

    async def previous_callback(interaction: discord.Interaction):
        """打开上一页心愿单。"""
        # 打开上一页心愿单。
        await view._go_to_page(interaction, view.page - 1)

    async def current_callback(interaction: discord.Interaction):
        """弹出当前心愿单的页码输入框。"""
        # 弹出当前心愿单的页码输入框。
        await interaction.response.send_modal(
            WishlistPageJumpModal(
                service=view.service,
                user_id=view.user_id,
                max_page=view.max_page,
            )
        )

    async def next_callback(interaction: discord.Interaction):
        """打开下一页心愿单。"""
        # 打开下一页心愿单。
        await view._go_to_page(interaction, view.page + 1)

    async def last_callback(interaction: discord.Interaction):
        """打开心愿单最后一页。"""
        # 打开心愿单最后一页。
        await view._go_to_page(interaction, view.max_page)

    first.callback = first_callback
    previous.callback = previous_callback
    current.callback = current_callback
    next_page.callback = next_callback
    last.callback = last_callback
    return discord.ui.ActionRow(first, previous, current, next_page, last)


def _remove_page_row(
    view: WishlistView,
    item_ids: tuple[int, ...],
) -> discord.ui.ActionRow:
    """为页面快照生成批量移除按钮。"""
    # 为页面快照生成批量移除按钮。
    remove_button = discord.ui.Button(
        label="移除本页全部项",
        style=discord.ButtonStyle.danger,
    )

    async def remove_callback(interaction: discord.Interaction):
        """按当前页面快照执行移除操作。"""
        # 按当前页面快照执行移除操作。
        await view._remove_page_items(interaction, item_ids)

    remove_button.callback = remove_callback
    return discord.ui.ActionRow(remove_button)


def _build_view(
    *,
    service: "WishlistService",
    user_id: int,
    page_data: "WishlistPage",
    include_raw_urls: bool,
    overflow_file: discord.File | None = None,
) -> WishlistView:
    """从分页 DTO 构建完整心愿单视图。"""
    # 从分页 DTO 构建完整心愿单视图。
    view = WishlistView(
        service=service,
        user_id=user_id,
        page=page_data.page,
        max_page=page_data.max_page,
    )

    if not page_data.entries:
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "## ❤️ 我的心愿单\n"
                    "心愿单还是空的。请在 `/下载` 面板选择资源版本后，"
                    "点击“加入心愿单”。"
                ),
                accent_color=discord.Color.blue(),
            )
        )
        return view

    for entry in page_data.entries:
        view.add_item(
            _resource_card(
                entry,
                view=view,
                include_raw_url=include_raw_urls,
            )
        )

    urls = [entry.url for entry in page_data.entries if entry.url]
    if overflow_file is None:
        url_block = "\n".join(urls) if urls else "本页没有可导出的 URL。"
        summary = (
            f"## ❤️ 我的心愿单 · 第 {page_data.page}/{page_data.max_page} 页\n"
            f"共 {page_data.total} 项；以下为本页批量导入 URL（每行一个）：\n"
            f"```\n{url_block}\n```\n"
            "-# 可使用 Discord 代码块的复制按钮一次复制；链接失效后请重新打开本页。"
        )
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(summary),
                accent_color=discord.Color.blurple(),
            )
        )
    else:
        view.add_item(
            discord.ui.TextDisplay(
                f"## ❤️ 我的心愿单 · 第 {page_data.page}/{page_data.max_page} 页\n"
                f"共 {page_data.total} 项。本页 URL 过长，请下载下方 TXT；"
                "文件内容严格每行一个 URL。"
            )
        )
        view.add_item(discord.ui.File(overflow_file))

    view.add_item(_pagination_row(view))
    item_ids = tuple(entry.item_id for entry in page_data.entries)
    view.add_item(_remove_page_row(view, item_ids))
    return view


def build_wishlist_render(
    *,
    service: "WishlistService",
    user_id: int,
    page_data: "WishlistPage",
) -> WishlistRender:
    """构建页面并在内容超长时提供 TXT 导出。"""
    # 构建页面并在内容超长时提供 TXT 导出。
    normal_view = _build_view(
        service=service,
        user_id=user_id,
        page_data=page_data,
        include_raw_urls=True,
    )
    if normal_view.content_length() <= 4000:
        return WishlistRender(view=normal_view)

    normal_view.stop()
    urls = [entry.url for entry in page_data.entries if entry.url]
    contents = ("\n".join(urls) + ("\n" if urls else "")).encode("utf-8")
    export_file = discord.File(
        io.BytesIO(contents),
        filename=f"wishlist-page-{page_data.page}-urls.txt",
    )
    overflow_view = _build_view(
        service=service,
        user_id=user_id,
        page_data=page_data,
        include_raw_urls=False,
        overflow_file=export_file,
    )
    if overflow_view.content_length() > 4000:
        raise ValueError("心愿单降级视图仍超过 Discord 的 4000 字符限制。")
    return WishlistRender(view=overflow_view, file=export_file)


def build_wishlist_consent_embed() -> discord.Embed:
    """构建心愿单存储协议告知内容。"""
    # 构建心愿单存储协议告知内容。
    return discord.Embed(
        title="❤️ 请阅读并同意心愿单数据存储声明",
        description=WISHLIST_POLICY_TEXT,
        color=discord.Color.blue(),
    )
