# Bot 架构设计方案 (v2)

本方案根据 `bot_plan.md` 的具体要求，为 Odysseia Protect Bot 设计一个健壮、可扩展的分层架构。该架构的核心目标是有效支撑 **普通/安全两种上传模式**，并确保 **数据一致性**。

## 核心架构：分层模式

我们将沿用分层架构，因为它能最好地将复杂流程分解为独立的、可管理的部分。

-   **`src/cogs` (表现层)**: 处理 Discord 命令和事件。
-   **`src/services` (业务逻辑层)**: 实现核心功能，如文件上传、下载逻辑。
-   **`src/database/repositories` (数据访问层)**: 封装数据库操作。
-   **`src/utils` (工具层)**: 提供通用辅助功能。

## 关键流程的架构实现

### 1. `/上传` 命令流程

此流程完美地体现了各层如何协作：

1.  **`cogs.upload`**:
    -   定义 `/上传` slash command，接收文件、版本信息、密码等参数。
    -   **职责**: 解析 Discord `Interaction` 对象，提取出纯粹的数据。
    -   调用 `services.ResourceService.upload_resource()`，将数据和 `mode` (普通/安全) 传递下去。

2.  **`services.ResourceService`**:
    -   `upload_resource(..., mode)` 方法是核心。
    -   **职责**: 根据 `mode` 参数执行不同的业务逻辑。
        -   **If `mode` is '安全模式'**:
            1.  调用 Discord API 在私密仓库频道创建一个新帖子 (需要一个 `DiscordService` 或类似的工具来封装API调用)。
            2.  获取新帖子的ID和URL。
            3.  调用 `repositories.ThreadRepository` 和 `repositories.ResourceRepository` 将帖子信息和资源元数据存入数据库。
        -   **If `mode` is '普通模式'**:
            1.  直接调用 `repositories.ThreadRepository` 和 `repositories.ResourceRepository` 存储资源信息，仓库帖子ID字段为 `NULL`。
    -   这个服务层封装了所有业务规则，对 Cog 层屏蔽了实现的复杂性。

### 2. `/下载` 命令流程

1.  **`cogs.download`**:
    -   定义 `/下载` 命令。
    -   调用 `services.ResourceService.get_resources_for_thread()`，传入当前帖子的ID。

2.  **`services.ResourceService`**:
    -   `get_resources_for_thread()` 调用 `repositories.ResourceRepository` 从数据库获取该帖子的所有资源版本。
    -   **职责**: 获取原始数据，为UI展现做准备。

3.  **`cogs.download` (返回)**:
    -   获取到资源列表后，调用 `utils.embed_factory.create_download_embed()`。
    -   **`utils.embed_factory`**: 这是一个关键的工具模块，负责构建包含下拉菜单和分页逻辑的 `discord.ui.View` 和 `discord.Embed`。它将数据转化为用户可见的UI。
    -   将生成的 `embed` 和 `view` 作为响应发送给用户。

### 3. `/管理附件` 命令流程

这个流程突显了架构如何处理数据同步的挑战：

1.  **`cogs.management`**:
    -   定义 `/管理附件` 命令，并通过 `utils` 模块构建管理界面的 `embed`。
    -   当作者在 `embed` 上进行操作（如点击“删除”按钮）时，Cog 接收事件。
    -   调用 `services.ResourceService.delete_resource()`，传入要删除的资源ID。

2.  **`services.ResourceService`**:
    -   `delete_resource()` 方法将执行一个**事务性**操作，以保证数据一致性。
    -   **职责**:
        1.  **开始事务**
        2.  调用 `repositories.ResourceRepository.get_by_id()` 获取资源信息，特别是它关联的仓库帖子中的消息ID。
        3.  调用 `repositories.ResourceRepository.delete()` 从数据库中删除记录。
        4.  **如果资源属于安全模式**: 调用 Discord API 删除仓库频道帖子中的对应消息。
        5.  **如果上述任何一步失败，则回滚数据库操作**，并向 Cog 层抛出异常。
        6.  **全部成功后，提交事务**。

## 总结

此架构的优势：

-   **清晰**: 每个模块职责单一，代码易于理解和定位。
-   **健壮**: 业务逻辑（尤其是有副作用和需要数据同步的操作）被集中在 `Service` 层，可以进行严谨的错误处理和事务管理。
-   **可测试**: 我们可以独立测试 `Service` 层的业务逻辑，而无需依赖一个运行中的 Discord Bot。
-   **可扩展**: 新功能可以方便地通过添加新的 Cog、Service 方法来集成，而不会破坏现有结构。