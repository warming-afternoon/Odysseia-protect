# Odysseia Protect 双层水印实验

这个目录验证了 SillyTavern Character Card V3 的首期溯源闭环：使用 AES-256-GCM 生成自包含凭证，并将同一凭证同时写入角色卡 JSON 扩展与 PNG 私有 Chunk。

## 当前协议

凭证明文仅在服务器生成时短暂存在：

```json
{
  "v": 1,
  "uid": "Discord User ID",
  "card_id": "官方作品 ID",
  "resource_id": "具体资源版本 ID",
  "issued_at": 1787846400,
  "delivery_id": "随机下载凭证 ID"
}
```

AES-GCM 信封：

```json
{
  "v": 1,
  "kid": "密钥版本",
  "nonce": "Base64URL 96-bit nonce",
  "ciphertext": "Base64URL ciphertext + authentication tag"
}
```

信封被写入：

- `data.extensions.odysseia_trace`：同时更新 `tEXt/chara` 与 `tEXt/ccv3`；
- `trAc`：PNG ancillary/private/safe-to-copy Chunk。

工具不会解码或重新编码图片像素，原始 `IHDR` 与全部 `IDAT` 字节保持不变。重复注入会替换旧凭证，不会叠加多个水印。

## 安装

项目已显式依赖 `cryptography`：

```bash
uv sync --extra test
```

## 使用

生产环境不要把密钥写进命令行。应使用仅 Bot 运行账号可读的 Secret 注入环境变量：

```bash
export ODYSSEIA_TRACE_KEY="$(uv run python -m Watermark.watermark keygen)"
```

检查卡片结构：

```bash
uv run python -m Watermark.watermark inspect Watermark/default_Seraphina.png
```

注入：

```bash
uv run python -m Watermark.watermark inject \
  Watermark/default_Seraphina.png \
  Watermark/default_Seraphina.watermarked.png \
  --key-id production-v1 \
  --user-id 123456789012345678 \
  --card-id official-card-id \
  --resource-id resource-version-id
```

严格核验：

```bash
uv run python -m Watermark.watermark verify \
  Watermark/default_Seraphina.watermarked.png \
  --expected-card-id official-card-id \
  --strict-layers
```

`--strict-layers`要求 PNG Chunk 与全部角色卡 JSON 副本都存在且一致。不启用严格模式时，只要任意一层残留且 AES-GCM 与 Card ID 校验通过，工具会返回 `degraded`，方便处理被清理了部分 Metadata 的泄露样本。

## 安全边界

- 核验时的 `expected_card_id`必须来自社区官方资源记录，不能相信泄露文件的文件名或外层可编辑字段。
- AES-GCM 能检测不知道密钥的第三方篡改，但持有对称密钥的核心开发者仍有解密和伪造能力。
- Card ID 绑定能识别跨作品移植，不能识别同一作品不同副本之间的水印移植。
- 结构层水印可以被图片重新保存工具完全清除。本实验已确认 macOS `sips` 重保存会同时删除角色卡 JSON和`trAc`。
- 当前工具只支持本次真实样本采用的 `tEXt/chara` 与 `tEXt/ccv3`。生产化前应收集更多V2/V3及不同导出工具的样本，再决定是否补充`zTXt/iTXt`。

## 自动化验证

```bash
uv run --extra test pytest -q tests/test_watermark.py
```

覆盖范围：真实卡往返、像素数据不变、错误密钥、密文篡改、跨卡校验、层间冲突、单层降级恢复与重复注入替换。

## LSB 像素层对比实验

像素层实验是独立的可选能力，不会随 Bot 的普通依赖安装：

```bash
uv run --extra watermark-experiment python -m Watermark.run_lsb_experiment
uv run --extra watermark-experiment --extra test pytest -q tests/test_lsb_watermark.py
```

实验比较结构水印、顺序 LSB、密钥散布三重复 LSB 与三层复合方案，并生成破坏存活矩阵、PSNR、处理耗时和20:00–00:00峰值带宽图。结果见 [`lsb_experiment_results/REPORT.md`](./lsb_experiment_results/REPORT.md)。
