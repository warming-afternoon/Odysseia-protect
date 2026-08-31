# Seraphina 真实角色卡水印实验报告

实验日期：2026-08-27  
输入文件：`default_Seraphina.png`  
输出文件：`default_Seraphina.watermarked.png`

## 样本结构

- PNG：400 × 600，8-bit RGBA，非隔行；
- Character Card：`chara_card_v3` / `3.0`；
- 原始 Chunk：`IHDR`、`IDAT`、`tEXt/chara`、`tEXt/ccv3`、`IEND`；
- `chara`与`ccv3`解码后为内容完全一致的 7176-byte JSON。

## 实验凭证

生成的实验副本使用虚构 UID 与公开测试密钥，不包含真实成员信息：

```json
{
  "v": 1,
  "uid": "999999999999999999",
  "card_id": "st-default-seraphina-v3",
  "resource_id": "experiment-resource-1",
  "issued_at": 1787846400,
  "delivery_id": "seraphina-experiment-001"
}
```

公开测试密钥仅用于复现实验，绝对不能用于生产：

```text
AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8
```

## 完整性结果

| 检查 | 原文件 | 水印文件 | 结论 |
|---|---|---|---|
| 文件大小 | 600890 bytes | 602137 bytes | 增加1247 bytes |
| 文件SHA-256 | `ccec7e9630fa958702b3180050ad060c522cdfd0807bba93524e911d1e4190f1` | `fd48e709e234369a5dc5a2b405e736570719ee280da01733f866f0f79021598d` | 文件按预期变化 |
| IDAT SHA-256 | `89f6bb13fc9b70ecca9107707764d3a2c7b4b0072d0cf3579fd648bdfe11d6bf` | 相同 | 像素压缩数据逐字节不变 |
| 图片属性 | 400×600 RGBA + Alpha | 相同 | 图片结构不变 |
| JSON载体 | `chara`、`ccv3` | 两者均有相同凭证 | 双副本一致 |
| PNG载体 | 无 | `trAc` 323 bytes | 注入成功 |

原图与水印图已分别渲染检查，视觉输出一致。自动化测试进一步验证：移除`odysseia_trace`后，角色卡JSON与原始JSON结构完全相等。

## 密码学与破坏校验

以下检查全部通过：

- 正确密钥、正确Card ID、三层一致时恢复完整载荷；
- 错误密钥触发AES-GCM认证失败；
- 同时篡改所有层的密文仍触发AES-GCM认证失败；
- PNG与JSON层内容冲突时拒绝核验；
- 预期Card ID不匹配时判定跨卡凭证无效；
- 删除`trAc`后可从JSON层降级恢复；
- 删除两份JSON扩展后可从`trAc`降级恢复；
- 严格模式会拒绝任何缺层样本；
- 对已水印文件再次注入只保留最新凭证和一个`trAc`。

测试结果：

```text
8 passed
```

## 性能结果

同一张约600 KB角色卡执行1000轮顺序注入与严格核验：

| 操作 | 平均 | P50 | P95 | 最大 |
|---|---:|---:|---:|---:|
| 双层注入 | 1.600 ms | 1.505 ms | 1.983 ms | 8.729 ms |
| 解密严格核验 | 0.599 ms | 0.558 ms | 0.763 ms | 4.239 ms |

`tracemalloc`观测峰值约2.53 MB。当前结构层实现的CPU与内存成本远低于Discord下载、VPS分发和网络传输成本。

## 已证实限制

使用macOS `sips`重新保存水印图片后：

- `chara`与`ccv3`均被删除；
- `trAc`被删除；
- IDAT被重新编码；
- 核验结果为“没有找到溯源凭证”。

因此“JSON扩展 + PNG Chunk”只能覆盖原文件转发、改名、复制和部分保留Metadata的工具链，不能抵抗主动重保存、截图、换图或Metadata清洗。频域像素水印仍需作为下一阶段独立实验，不能用本轮结果宣称抗清洗。

## 尚需人工验证

自动化证据已经证明PNG结构、V3 JSON和像素数据保持正确，但当前环境没有运行SillyTavern客户端。仍需将`default_Seraphina.watermarked.png`实际导入ST，确认角色字段、世界书与扩展功能的客户端行为无异常；随后再从ST导出，检查它保留了哪些水印层。

