# 数据库设计方案

## 概述

本数据库旨在支持 Bot 的文件分发功能。核心设计思想是分离**帖子（Threads）**和**资源（Resources）**, 以便一个帖子可以管理多个不同版本的资源。

---

## 数据表结构

### 1. `Threads` - 帖子关联表

这个表的核心作用是建立公开论坛帖子和私密仓库帖子之间的一对一关联。

| 字段名                | 类型        | 描述                             | 约束                 |
| --------------------- | ----------- | -------------------------------- | -------------------- |
| `id`                  | `INTEGER`   | 数据表主键                       | `PRIMARY KEY`        |
| `public_thread_id`    | `BIGINT`    | 公开论坛的帖子 ID                | `UNIQUE`, `NOT NULL` |
| `warehouse_thread_id` | `BIGINT`    | (安全模式) 私密仓库频道的帖子 ID | `UNIQUE`, `NULLABLE` |
| `author_id`           | `BIGINT`    | 资源发布者的 Discord 用户 ID     | `NOT NULL`           |
| `mode`                | `TEXT`      | 模式 ('secure' 或 'normal')      | `NOT NULL`           |
| `created_at`          | `TIMESTAMP` | 记录创建时间                     | `DEFAULT NOW()`      |

**说明:**
*   `mode` 字段用于区分两种工作模式。
*   当 `mode` 为 `normal` 时, `warehouse_thread_id` 为 `NULL`。
*   `author_id` 用于权限验证，确保只有作者本人才能管理其发布的资源。

---

### 2. `Resources` - 资源信息表

这个表用于存储每一个上传的独立文件（或版本）的详细信息。

| 字段名              | 类型        | 描述                                 | 约束            |
| ------------------- | ----------- | ------------------------------------ | --------------- |
| `id`                | `INTEGER`   | 数据表主键                           | `PRIMARY KEY`   |
| `thread_id`         | `INTEGER`   | 关联到 `Threads` 表的 `id`           | `FOREIGN KEY`   |
| `version_info`      | `TEXT`      | 作者填写的版本信息或文件描述         | `NOT NULL`      |
| `download_url`      | `TEXT`      | 附件的实际下载链接 (Discord CDN URL) | `NOT NULL`      |
| `password`          | `TEXT`      | (可选) 下载附件所需的密码            | `NULLABLE`      |
| `source_message_id` | `BIGINT`    | 包含该附件的源消息 ID                | `NOT NULL`      |
| `filename`          | `TEXT`      | 文件名                               | `NULLABLE`      |
| `created_at`        | `TIMESTAMP` | 记录创建时间                         | `DEFAULT NOW()` |

**说明:**
*   `thread_id` 是一个外键，它将每个资源精确地关联到一个帖子上。
*   `source_message_id` 指向包含附件的消息。Bot 的业务逻辑需要根据 `Threads` 表中的 `mode` 来判断此 ID 属于公共帖子还是私密仓库帖子。

---

## 关系图 (E-R Diagram)

```
[Threads] 1--* [Resources]
  |              |
  |              +-- thread_id (FK)
  +-- id (PK)
  +-- public_thread_id
  +-- warehouse_thread_id
  +-- author_id
```

这个设计满足了您的所有需求：
1.  **关联性:** `Threads` 表通过 `public_thread_id` 和 `warehouse_thread_id` 字段将两个帖子关联起来。
2.  **资源URL:** `Resources` 表中的 `download_url` 字段存储了每个资源的链接。
3.  **可扩展性:** 一个 `Thread` 可以关联多个 `Resource`，完美支持多版本管理。