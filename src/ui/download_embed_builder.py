import discord

from src.dto.download_page_dto import DownloadPageDTO
from src.dto.resource_dto import ResourceDTO
from src.enums import UploadMode
from src.services.delivery_service import DeliveryResult


class DownloadEmbedBuilder:
    """构建当前资源页与下载结果的展示内容。"""

    @staticmethod
    def build_page_embed(page: DownloadPageDTO) -> discord.Embed:
        """仅展示当前页资源并按上传模式分组。"""
        if not page.items:
            return discord.Embed(
                title="📂 暂无资源", description="这个帖子还没有可下载的资源。",
                color=discord.Color.blue(),
            )
        embed = discord.Embed(
            title="📄 版本选择",
            description="请选择下面的资源版本进行下载，最新上传的版本排在前面。",
            color=discord.Color.green(),
        )
        # 按模式分组当前页，控制每个字段的文字长度。
        for mode, name in ((UploadMode.SECURE, "🔒 受保护资源"), (UploadMode.NORMAL, "📄 资源")):
            chunks = []
            chunk = ""
            for resource in page.items:
                if resource.upload_mode != mode:
                    continue
                version = discord.utils.escape_markdown(resource.version_info or "未命名")[:30]
                filename = discord.utils.escape_markdown(resource.filename or "N/A")[:30]
                icon = "🔎" if resource.trace_enabled else "🔹"
                line = f"{icon} **{version}** (`{filename}`)"
                if mode == UploadMode.NORMAL and page.guild_id:
                    url = f"https://discord.com/channels/{page.guild_id}/{page.public_thread_id}/{resource.source_message_id}"
                    line += f" - [跳转]({url})"
                if len(chunk) + len(line) + 1 > 1000:
                    chunks.append(chunk)
                    chunk = ""
                chunk += ("\n" if chunk else "") + line
            if chunk:
                chunks.append(chunk)
            for index, value in enumerate(chunks):
                embed.add_field(name=name if index == 0 else name + " (续)", value=value, inline=False)
        embed.set_footer(text=f"第 {page.page}/{page.max_page} 页 · 共 {page.total} 个版本")
        return embed

    @staticmethod
    def build_download_embed(resource: ResourceDTO, fresh_url: str) -> discord.Embed:
        """构建同时适合直接下载和复制到 SillyTavern 的结果页。"""
        # 在结果页同时提供下载链接和可复制的导入地址。
        embed = discord.Embed(
            title="📥 角色卡下载",
            description=(
                f"**版本：** {resource.version_info}\n"
                f"**文件：** `{resource.filename or '未命名文件'}`\n\n"
                "📋 **SillyTavern 快速导入 URL**\n"
                f"```\n{fresh_url}\n```\n"
                f"[🌐 打开下载链接]({fresh_url})\n\n"
                "链接具有时效性；失效后请重新打开下载面板获取。"
            ),
            color=discord.Color.green(),
        )
        filename = (resource.filename or "").lower()
        if filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            embed.set_image(url=fresh_url)
        return embed

    @staticmethod
    def build_delivery_embed(
        resource: ResourceDTO, delivery: DeliveryResult
    ) -> discord.Embed:
        """构建链接或附件方式的个性化资源下载结果。"""
        # 根据交付是否有公开有效链接选择展示内容。
        if delivery.url:
            return DownloadEmbedBuilder.build_download_embed(resource, delivery.url)
        return discord.Embed(
            title="📥 个性化角色卡下载",
            description=(
                f"**版本：** {resource.version_info}\n"
                f"**文件：** `{delivery.filename}`\n\n"
                "R2 当前不可用，已改用本条私密消息的附件交付。\n"
                "该附件仅包含为当前用户生成的溯源凭证。"
            ),
            color=discord.Color.orange(),
        )
