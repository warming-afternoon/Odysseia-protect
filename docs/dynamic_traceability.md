# 动态溯源部署与运维

## 数据流

普通资源继续直接返回 Discord 原附件 URL。只有资源的 `trace_enabled` 为真时，Bot 才会在用户明确确认后读取仓库源文件、生成个性化 PNG，并通过私有 R2 交付。

缓存键由用户 ID、资源 ID、源消息 ID 和密钥版本共同决定。同一用户在滑动 30 分钟内重复请求同一版本时，不会再次加密或上传；Bot 只重新签发 URL并把对象删除时间延长为“当前时间 + 30 分钟 + 5 分钟宽限”。

## 必需配置

1. 将 `.env.example` 中 `TRACEABILITY_ENABLED`、`ODYSSEIA_TRACE_*`、`TRACE_ADMIN_*`、`R2_*` 和 `REDIS_URL` 填写完整。
2. 使用以下命令生成 32 字节 Base64URL 密钥，并将输出安全保存：

   ```bash
   python -m src.traceability.watermark keygen
   ```

3. R2 桶必须保持私有。API Token 只授予目标桶的对象读写权限，不要公开 Access Key、Secret Key 或 AES 密钥。
4. 在 Cloudflare R2 控制台为同一桶配置两条对象生命周期规则：

   | 前缀 | 过期时间 | 用途 |
   | --- | ---: | --- |
   | `deliveries/` | 1 天 | Bot/Redis/清理任务故障时回收临时交付对象 |
   | `reports/` | 7 天 | 回收管理员核验报告 |

生命周期按天执行，不保证在精确时刻删除，也不控制下载权限。正常情况下，Bot 每分钟从 Redis Sorted Set 读取到期 Key，并用一次 `DeleteObjects` 批量删除最多 1000 个对象；不会扫描或列举 R2。签名 URL 独立在 30 分钟后失效。

## 部署顺序

```bash
docker compose build
docker compose --profile migrate run --rm odysseia-protect-migrate
docker compose up -d odysseia-protect-redis odysseia-protect
```

首次上线保持 `TRACEABILITY_ENABLED=false`。确认数据库迁移、Redis 健康、R2 权限和生命周期规则后，再改为 `true`，先为少量测试资源开启溯源。

## 管理员核验

`/溯源 核验` 直接打开 Modal。File Upload 一次接受 1～10 个附件，每个附件独立产生 `TR-...` 任务号、中文 Markdown 报告和技术 JSON 报告。即时响应会附上两份文件；R2 则保存包含两份报告的 ZIP。

- 支持：PNG、ZIP、7z。
- 只有不超过 25 MiB 的 PNG 角色卡可以在上传时开启动态溯源；关闭溯源的受保护资源不受此限制。
- 不支持：带密码或加密压缩包、嵌套压缩包、符号链接和不安全路径。
- 限制：最多 1000 项、解压总量 3 GiB、单项 25 MiB、压缩比不超过 100:1。
- 非 PNG 项会写入报告为 `skipped`，不会导致整个压缩包失败。
- 正常完成会立即发送 Ephemeral 报告；原 interaction 过期后使用 `/溯源 报告 report_id` 获取。
- 技术 JSON 使用 `downloader_discord_user_id` 表示下载者；水印内部继续使用兼容字段 `uid`。
- 报告只提供调查证据，不执行自动封禁或处罚。

## 监控建议

- `deliveries/` 临时占用超过 5 GB；
- 最老临时对象超过 2 小时；
- Redis ping 失败；
- R2 上传、签名或批量删除连续失败；
- 核验任务长期停留在 `queued` 或 `processing`。

Redis 故障时会退化为单进程缓存；R2 故障时直接下载会退化为 Discord Ephemeral 个性化附件。进程崩溃后遗留的 R2 对象最终由 1 天生命周期回收。
