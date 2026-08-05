# artifacts/upgrade-smoke 升级验收摘要

## 验收信息
- 验收时间：2026-08-03 17:13 (Asia/Shanghai)
- 验收目标：`artifacts/upgrade-smoke`
- 验收方式：四步验收流程（检查 permission.txt → 生成 summary.md → 敏感信息检查 → 交叉核对）

## 检查文件清单
| 文件 | 相对路径 | 状态 |
| --- | --- | --- |
| permission.txt | `artifacts/upgrade-smoke/permission.txt` | 存在，可读 |
| summary.md | `artifacts/upgrade-smoke/summary.md` | 本次生成/覆盖 |

## permission.txt 原样内容
```text
agentscope-2.0.5 permission check
```

## 内容核验结果
- 版本号：包含 `agentscope-2.0.5`，符合预期基线。
- 标记：包含 `permission` 标记，符合预期。
- 完整性：文件共 33 字节、单行、无结尾换行；无乱码、无隐藏字符、无多余空行。
- 预期内容对比：`agentscope-2.0.5 permission check`，与实际内容完全一致。

## 敏感信息检查
- 检查范围：summary.md 全文。
- 检查特征：api key、sk-、token、secret、password、passwd、AKIA、私钥（BEGIN ... PRIVATE KEY）等凭据形态。
- 检查结果：未发现敏感信息。

## 验收结论
**通过（PASS）**
