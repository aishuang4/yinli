# Proposal

## Why

除 `yinli-catalog` 外，Skill 必须写进每个 Agent 的 `skills:` 才进入 Runtime。Word、图表和项目包因此要重复勾选；换一个助手就丢能力。用户需要的是 Skill 上的一个开关：打开则合格 Agent 都能用，关闭则仍需手动加到 Agent。

## What Changes

- Skills 增加布尔开关（建议字段 `availableToAllAgents`），与现有「安装 / 启用」分开：启用表示包可用，开关表示要不要绕过 Agent 绑定。
- 开关打开且 Skill 仍已安装、已启用时，单 Agent Chat（含 Sandbox）与 Team Leader 的 Runtime 目录自动包含该 Skill，不必出现在 Agent profile。
- 开关关闭时保持现状：必须写在该 Agent 的 `skills:` 里才注入目录。
- 正文仍只来自本地 `SKILL.md`，按需用 Skill 工具读取；不预灌全文进 system prompt。
- `yinli-catalog` 继续无条件进入合格 Chat 目录（开关视为锁定打开，不能关成「无人可用」）。
- 默认打开：新安装与旧库升级后，已启用 Skill 对合格 Chat Agent 共享；只要单人使用再关掉开关。
- Job Agent、群聊 Worker / Host、以及 `include_default_tools=False` 的基础设施 Agent **不**因开关自动获得 Skill。定时任务仍只认冻结的 `skill_ids`。

## Capabilities

### New Capabilities

- `skills`：Skill 目录如何进入 Chat Runtime，以及「启用」与「所有 Agent 可用」两个开关的对外契约。

### Modified Capabilities

- （无。现有主 spec 只有 `group-studio`，本变更不改群聊要求。）

## Impact

- 后端：`Skill` 模型与 `/api/skills` 读写新字段；`run_context._merge_file_catalog_skills` / `select_runtime_skills` 按开关合并目录；sqlite `skills` 与外部包 override 持久化开关。
- 前端：Skills 详情/列表增加「所有 Agent 可用」；Agent 编辑器在开关打开时标明该技能已共享，无需再勾选。
- 文档：`docs/file-catalog-v1.md` §9.2 / §13.2 与 `AGENTS.md` 中「必须出现在 Agent 配置」的表述。
- 不改：Skill 正文权威仍是文件；不新增系统 CRUD 工具；不恢复旧 memory runtime；群聊 Worker 工具集不变。
