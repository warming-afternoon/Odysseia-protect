import discord

from src.enums.download_panel_mode import DownloadPanelMode
from src.qo.download_page_qo import DownloadPageQo
from src.ui.download_embed_builder import DownloadEmbedBuilder
from src.ui.public_resource_select_view import PublicResourceSelectView
from src.ui.resource_select_view import ResourceSelectView


async def build_download_panel(service, session, *, source, panel_mode=DownloadPanelMode.PRIVATE):
    """从服务取得分页数据并构建下载入口响应。"""
    # 校验 Discord 来源后仅通过服务取得数据对象。
    if not isinstance(source.channel, (discord.TextChannel, discord.Thread)):
        return {"embed": discord.Embed(
            title="❌ 操作无效", description="此命令只能在服务器的文本频道或帖子中使用。",
            color=discord.Color.red(),
        )}
    page_data = await service.get_page(session, DownloadPageQo(public_thread_id=source.channel.id))
    # 旧帖子尚未记录服务器元数据时使用当前消息的服务器信息。
    if page_data.guild_id is None and source.guild is not None:
        page_data = page_data.model_copy(update={"guild_id": source.guild.id})
    if not page_data.items:
        return {"embed": DownloadEmbedBuilder.build_page_embed(page_data)}
    # 保留公开入口与私密入口的原有消息响应方式。
    if panel_mode is DownloadPanelMode.PUBLIC_GATEWAY:
        view = PublicResourceSelectView(page_data)
    else:
        view = ResourceSelectView(page_data, user_id=source.user.id)
    return {"embed": view.resource_list_embed, "view": view}
