# Odysseia Protect

一个用于 Discord 的文件管理和保护机器人，支持两种上传模式：**普通资源**和**受保护资源**，并提供完整的资源管理、下载、反应墙等功能。

## 功能特性

- **双模式上传**
  - **普通文件**：引用帖子内已有的消息附件，仅记录位置，不存储副本。
  - **受保护文件**：将文件上传至私密仓库频道，提供有时效性的下载链接，支持密码保护。

- **完整的资源管理**
  - `/上传` – 上传新资源（普通/受保护）。
  - `/下载` – 获取当前帖子的资源列表并下载。
  - `/管理` – 管理已上传的资源（编辑、删除、开启/关闭反应墙）。
  - `/使用手册` – 显示详细的使用手册。

- **反应墙（Reaction Wall）**
  - 可要求用户在下载受保护资源前对帖子起始消息做出反应。
  - 支持自定义反应表情，或允许任意表情。
  - 仅对受保护资源生效。

- **隐私协议**
  - 首次使用 Bot 时需同意隐私协议，确保用户了解数据存储与使用规则。

- **数据一致性**
  - 删除受保护资源时，会同时删除仓库频道中的文件副本，确保数据同步。

## 技术架构

项目采用分层架构，职责清晰，易于维护和扩展：

```
src/
├── cogs/                    # Discord 命令层（表现层）
│   ├── upload_cog.py       # /上传 命令
│   ├── download_cog.py     # /下载 命令
│   ├── manage_cog.py       # /管理 命令
│   └── info_cog.py         # /使用手册 命令
├── services/               # 业务逻辑层
│   ├── upload_service.py   # 上传业务逻辑
│   ├── download_service.py # 下载业务逻辑
│   ├── management_service.py # 管理业务逻辑
│   └── reaction_wall_service.py # 反应墙逻辑
├── database/               # 数据访问层
│   ├── models.py          # SQLAlchemy ORM 模型
│   ├── repositories/      # 仓库模式封装数据库操作
│   └── schemas.py         # Pydantic 数据验证模型
├── ui/                     # UI 组件层
│   ├── upload_ui.py       # 上传相关的 Modal 和 View
│   ├── download_ui.py     # 下载相关的 View
│   └── management_ui.py   # 管理相关的 View
└── utils/                  # 工具层
    ├── discord_utils.py   # Discord 相关工具函数
    └── formatting.py      # 格式化工具
```

## 快速开始

### 环境要求

- Python 3.8+
- Discord Bot Token
- SQLite（默认）或 PostgreSQL

### 安装步骤

1. **克隆仓库**
   ```bash
   git clone <repository-url>
   cd Odysseia-protect
   ```

2. **安装依赖**
   ```bash
   uv sync
   ```

3. **配置环境变量**
   复制 `.env.example` 为 `.env` 并填写：
   ```env
   DISCORD_BOT_TOKEN=your_bot_token_here
   WAREHOUSE_CHANNEL_ID=your_warehouse_forum_channel_id
   TEST_GUILD_ID=your_test_guild_id
   ```

4. **初始化数据库**
   ```bash
   python -m src.database.database
   ```
   或通过运行 Bot 自动初始化。

5. **运行 Bot**
   ```bash
   python main.py
   ```

## 命令详解

### `/上传`
上传一个新资源。

- **参数**：
  - `mode`：上传类型（普通文件/受保护文件）。
  - `file`（受保护文件）：直接上传附件。
  - `message_link`（普通文件）：粘贴消息链接。

- **流程**：
  1. 检查隐私协议（首次使用）。
  2. 验证用户是否为帖子作者。
  3. 根据模式弹出相应表单填写版本信息（和密码）。
  4. 完成上传并记录到数据库。

### `/下载`
获取当前帖子的资源列表。

- **流程**：
  1. 检查当前帖子是否有资源。
  2. 按模式分组显示（受保护资源/普通资源）。
  3. 对于受保护资源，提供下拉菜单选择下载。
  4. 若启用反应墙，验证用户是否已对起始消息做出反应。
  5. 若资源有密码，弹出密码验证模态框。
  6. 验证通过后，动态生成有时效性的下载链接。

### `/管理`
管理当前帖子的资源和设置。

- **功能**：
  - 查看资源列表（按模式分组）。
  - 编辑资源信息（版本、密码）。
  - 删除资源（受保护资源会同时删除仓库文件）。
  - 开启/关闭反应墙。
  - 设置自定义反应表情。

### `/使用手册`
显示详细的使用手册（内容来自 `src/config.py`）。

## 数据库设计

使用 SQLAlchemy ORM，包含三个核心表：

- **threads**：帖子关联信息。
  - `public_thread_id`：公开帖子 ID。
  - `warehouse_thread_id`：对应的私密仓库帖子 ID（仅受保护文件使用）。
  - `author_id`：帖子作者 Discord ID。
  - `reaction_required`：是否启用反应墙。
  - `reaction_emoji`：自定义反应表情。

- **resources**：资源信息。
  - `thread_id`：所属帖子 ID。
  - `upload_mode`：上传模式（SECURE/NORMAL）。
  - `filename`：文件名。
  - `version_info`：版本信息。
  - `source_message_id`：源消息 ID（普通文件为公开消息 ID，受保护文件为仓库消息 ID）。
  - `password`：下载密码（可选）。

- **users**：用户信息。
  - `id`：Discord 用户 ID。
  - `has_agreed_to_privacy_policy`：是否同意隐私协议。

## 开发指南

### 添加新功能

1. **新增命令**：在 `src/cogs/` 下创建新的 Cog。
2. **新增业务逻辑**：在 `src/services/` 下编写 Service 类。
3. **新增 UI 组件**：在 `src/ui/` 下创建 View 或 Modal。
4. **数据库变更**：使用 Alembic 创建迁移脚本。

### 运行测试

```bash
pytest
```

### 代码风格

- 使用 Black 格式化代码。
- 使用 isort 排序导入。
- 遵循类型注解（使用 `mypy` 检查）。

## 配置说明

### 环境变量

| 变量名                 | 说明              | 示例         |
| ---------------------- | ----------------- | ------------ |
| `DISCORD_BOT_TOKEN`    | Discord Bot Token | `MTE...`     |
| `WAREHOUSE_CHANNEL_ID` | 仓库论坛频道 ID   | `1234567890` |
| `TEST_GUILD_ID`        | 测试服务器 ID     | `9876543210` |

### 配置文件

`src/config.py` 包含隐私协议文本和使用手册文本，可根据需要修改。

## 常见问题

### 1. 受保护文件上传失败
- 检查 `WAREHOUSE_CHANNEL_ID` 是否配置正确，且 Bot 有权限访问该论坛频道。
- 确保仓库频道是 **论坛频道（Forum Channel）**。

### 2. 反应墙不生效
- 反应墙仅对 **受保护资源** 生效。
- 确保已正确设置反应表情（或留空允许任意表情）。
- 检查 Bot 是否有读取消息反应的权限。

### 3. 下载链接过期
- Discord 附件链接默认有时效性（约 24 小时）。
- Bot 在下载时动态获取最新链接，确保链接有效。

### 4. 数据库迁移
若修改了模型，使用 Alembic 生成迁移：
```bash
alembic revision --autogenerate -m "描述"
alembic upgrade head
```

## 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

## 贡献

欢迎提交 Issue 和 Pull Request。

## 联系方式

如有问题或建议，请通过 Discord 或项目仓库联系。