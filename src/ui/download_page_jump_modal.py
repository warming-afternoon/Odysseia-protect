import discord


class DownloadPageJumpModal(discord.ui.Modal, title="跳转到资源页面"):
    """输入页码并跳转到对应资源页。"""

    def __init__(self, panel_view):
        """记录目标面板并创建页码输入框。"""
        super().__init__(timeout=300)
        self.panel_view = panel_view
        self.max_page = panel_view.page_data.max_page
        self.page_input = discord.ui.TextInput(
            label=f"页码（1-{self.max_page}）", placeholder="请输入要跳转的页码",
            required=True, min_length=1, max_length=len(str(self.max_page)),
        )
        self.add_item(self.page_input)

    async def on_submit(self, interaction: discord.Interaction):
        """验证输入页码并委托面板执行跳转。"""
        # 先校验所属用户与输入范围，错误输入不改变原面板。
        if not await self.panel_view.interaction_check(interaction):
            return
        try:
            page = int(self.page_input.value)
        except ValueError:
            page = 0
        if not 1 <= page <= self.max_page:
            await interaction.response.send_message(
                f"❌ 请输入 1 到 {self.max_page} 之间的整数页码。", ephemeral=True,
            )
            return
        await self.panel_view.go_to_page(interaction, page)
