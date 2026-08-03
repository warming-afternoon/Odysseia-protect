import io
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from src.config import WISHLIST_POLICY_TEXT
from src.database.database import AsyncSessionLocal
from src.enums import SourceStatus

if TYPE_CHECKING:
    from src.services.wishlist_service import WishlistPage, WishlistService

logger = logging.getLogger(__name__)


@dataclass
class WishlistRender:
    view: discord.ui.LayoutView
    file: discord.File | None = None


class WishlistPageJumpModal(discord.ui.Modal, title="跳转到心愿单页面"):
    def __init__(
        self,
        *,
        service: "WishlistService",
        user_id: int,
        max_page: int,
    ):
        super().__init__(timeout=300)
        self.service = service
        self.user_id = user_id
        self.max_page = max_page
        self.page_input = discord.ui.TextInput(
            label=f"页码（1-{max_page}）",
            placeholder="请输入要跳转的页码",
            min_length=1,
            max_length=len(str(max_page)),
        )
        self.add_item(self.page_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            page = int(self.page_input.value)
        except ValueError:
            page = 0
        if not 1 <= page <= self.max_page:
            await interaction.response.send_message(
                f"❌ 请输入 1 到 {self.max_page} 之间的页码。",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        async with AsyncSessionLocal() as session:
            render = await self.service.build_render(
                session,
                user_id=self.user_id,
                page=page,
            )
        attachments = [render.file] if render.file else []
        await interaction.edit_original_response(
            view=render.view,
            attachments=attachments,
        )


class WishlistView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        service: "WishlistService",
        user_id: int,
        page: int,
        max_page: int,
    ):
        super().__init__(timeout=14400)
        self.service = service
        self.user_id = user_id
        self.page = page
        self.max_page = max_page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "❌ 这不是您的心愿单面板。",
            ephemeral=True,
        )
        return False

    async def _go_to_page(self, interaction: discord.Interaction, page: int):
        await interaction.response.defer()
        async with AsyncSessionLocal() as session:
            render = await self.service.build_render(
                session,
                user_id=self.user_id,
                page=page,
            )
        attachments = [render.file] if render.file else []
        await interaction.edit_original_response(
            view=render.view,
            attachments=attachments,
        )

    async def _remove_and_refresh(
        self,
        interaction: discord.Interaction,
        *,
        resource_id: int | None = None,
        item_ids: tuple[int, ...] = (),
    ):
        await interaction.response.defer()
        try:
            async with AsyncSessionLocal() as session:
                try:
                    if resource_id is not None:
                        await self.service.remove(
                            session,
                            user_id=self.user_id,
                            resource_id=resource_id,
                        )
                    else:
                        await self.service.remove_items(
                            session,
                            user_id=self.user_id,
                            item_ids=item_ids,
                        )
                    render = await self.service.build_render(
                        session,
                        user_id=self.user_id,
                        page=self.page,
                    )
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
        except Exception:
            logger.exception("从心愿单移除项目失败")
            await interaction.followup.send(
                "❌ 移除心愿单项目时发生内部错误，请稍后重试。",
                ephemeral=True,
            )
            return

        attachments = [render.file] if render.file else []
        await interaction.edit_original_response(
            view=render.view,
            attachments=attachments,
        )
        self.stop()

    async def _remove_resource(
        self,
        interaction: discord.Interaction,
        resource_id: int,
    ):
        await self._remove_and_refresh(
            interaction,
            resource_id=resource_id,
        )

    async def _remove_page_items(
        self,
        interaction: discord.Interaction,
        item_ids: tuple[int, ...],
    ):
        await self._remove_and_refresh(
            interaction,
            item_ids=item_ids,
        )

    async def on_timeout(self):
        # 私密交互令牌可能已经失效，不再尝试编辑消息。
        pass


def _resource_card(
    entry,
    *,
    view: WishlistView,
    include_raw_url: bool,
) -> discord.ui.Container:
    resource = entry.resource
    version = discord.utils.escape_markdown(resource.version_info or "未命名")
    filename = discord.utils.escape_markdown(resource.filename or "未命名文件")
    source_name = resource.public_thread_name or (
        f"未知帖子（ID: {resource.public_thread_id}）"
    )
    source_label = discord.utils.escape_markdown(source_name).replace(
        "]", "\\]"
    )
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
        await view._go_to_page(interaction, 1)

    async def previous_callback(interaction: discord.Interaction):
        await view._go_to_page(interaction, view.page - 1)

    async def current_callback(interaction: discord.Interaction):
        await interaction.response.send_modal(
            WishlistPageJumpModal(
                service=view.service,
                user_id=view.user_id,
                max_page=view.max_page,
            )
        )

    async def next_callback(interaction: discord.Interaction):
        await view._go_to_page(interaction, view.page + 1)

    async def last_callback(interaction: discord.Interaction):
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
    remove_button = discord.ui.Button(
        label="移除本页全部项",
        style=discord.ButtonStyle.danger,
    )

    async def remove_callback(interaction: discord.Interaction):
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


class WishlistConsentView(discord.ui.View):
    """首次加入心愿单前的协议确认。"""

    def __init__(
        self,
        *,
        service: "WishlistService",
        user_id: int,
        resource_id: int,
        panel_view,
        panel_message: discord.Message | None,
    ):
        super().__init__(timeout=300)
        self.service = service
        self.user_id = user_id
        self.resource_id = resource_id
        self.panel_view = panel_view
        self.panel_message = panel_message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "❌ 这不是您的协议确认面板。",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="同意并加入", style=discord.ButtonStyle.success)
    async def agree(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.defer(ephemeral=True)
        async with AsyncSessionLocal() as session:
            try:
                result = await self.service.accept_policy_and_add(
                    session,
                    user_id=self.user_id,
                    resource_id=self.resource_id,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("同意协议并加入心愿单失败")
                await interaction.edit_original_response(
                    content="❌ 加入心愿单时发生内部错误。",
                    embed=None,
                    view=None,
                )
                return

        if result == "not_found":
            await interaction.edit_original_response(
                content="❌ 资源已不存在，无法加入心愿单。",
                embed=None,
                view=None,
            )
            return

        selection_is_still_current = (
            getattr(self.panel_view, "selected_resource_id", None)
            == self.resource_id
        )
        if selection_is_still_current:
            self.panel_view.set_wishlist_state(True)
        if selection_is_still_current and self.panel_message is not None:
            try:
                await self.panel_message.edit(view=self.panel_view)
            except discord.HTTPException:
                logger.warning("无法刷新原下载面板的心愿单按钮状态")

        await interaction.edit_original_response(
            content=(
                "✅ 已加入心愿单！\n"
                "使用 `/心愿单`，或右键任意服务器消息 → Apps → “打开心愿单”查看。\n"
                "每页最多 6 项，页末 URL 可一键复制并粘贴到 "
                "SillyTavern 批量导入；链接失效后重新打开即可刷新。"
            ),
            embed=None,
            view=None,
        )

    @discord.ui.button(label="拒绝", style=discord.ButtonStyle.danger)
    async def disagree(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.edit_message(
            content="⚠️ 您已拒绝协议，资源未加入心愿单。",
            embed=None,
            view=None,
        )


def build_wishlist_consent_embed() -> discord.Embed:
    return discord.Embed(
        title="❤️ 请阅读并同意心愿单数据存储声明",
        description=WISHLIST_POLICY_TEXT,
        color=discord.Color.blue(),
    )
