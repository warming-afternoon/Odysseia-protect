"""心愿单分页设置与共用提示文案，不依赖服务或界面层。"""

WISHLIST_PAGE_SIZE = 6
"""每页展示的心愿单项目数量，分页查询和使用提示共用此值。"""

WISHLIST_USAGE = (
    "使用 `/心愿单`，或右键任意服务器消息 → Apps → “打开心愿单”查看。\n"
    f"每页最多 {WISHLIST_PAGE_SIZE} 项，页末 URL 可一键复制并粘贴到 "
    "SillyTavern 批量导入；链接失效后重新打开即可刷新。"
)
"""加入心愿单后展示的入口、批量导入与链接刷新说明。"""

WISHLIST_ADDED_MESSAGE = f"✅ 已加入心愿单！\n{WISHLIST_USAGE}"
"""直接加入或同意协议后加入成功时共用的完整提示。"""
