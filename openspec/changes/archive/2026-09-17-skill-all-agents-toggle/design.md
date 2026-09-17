# Design

## Context

现行装配在 `run_context`：`configured_skill_ids` 来自 Agent profile，再经 Project 白名单与 `_merge_file_catalog_skills`。除 `ALWAYS_INJECTED_SKILL_IDS`（`yinli-catalog`）外，系统包和项目 `.agents/skills` 都必须出现在 Agent `skills:`。正文权威是本地 `SKILL.md`（`_hydrate_skill`），sqlite `skills.content` 不信任。Skills 页已有「启用」开关（`POST /{id}/enable`），与本次「是否绕过 Agent 绑定」不是同一件事。动机见 proposal.md；行为见 specs。

## Goals / Non-Goals

**Goals:**

- 用一个持久布尔字段表达「所有合格 Chat Agent 可用」，装配时与 profile 列表取并集。
- 默认 true，新装与旧库升级后已启用 Skill 对合格 Chat 共享；`yinli-catalog` 继续锁定共享。
- UI 把「启用」和「所有 Agent 可用」拆开，避免一个开关干两件事。

**Non-Goals:**

- 不预灌全部 `SKILL.md`。
- 不改 Job 冻结集、群聊 Worker 工具、基础设施无工具 Agent。
- 不把用户主目录未启用包自动变成共享。
- 不在 Agent 上再做「禁用某个全局 Skill」黑名单。

## Decisions

### D1. 字段名 `availableToAllAgents`

与 `enabled` 并列。语义：包已启用时，要不要加入合格 Chat 目录而不看 profile。

备选：复用 `enabled` 表示共享。否决：启用今天只表示「安装后开着」，关掉会让已绑定的 Agent 也丢包。

备选：只改 Agent 默认勾选所有 Skill。否决：新 Agent / 新包仍要维护，且污染 Job profile。

### D2. 合格 Runtime 白名单

共享目录只合并进：

- `mode=agent` 的 Chat / Sandbox（`require_agent` 已通过的用户 Chat Agent，含 `default` 助手）
- `mode=team` 的 **Leader**（沿用当前选中的 Chat Agent）

明确排除：`role=job`、群聊 Worker / Host、`include_default_tools=False`、Team Member session（`member_runtime` 今天 `used_skills=[]`，保持）。定时任务 `frozen_skill_ids` 不读此开关。

Team 今日 `_merge_file_catalog_skills(..., inject=mode!="team")` 会跳过 Leader 的 `yinli-catalog` 追加。本变更把「共享集合」对 Leader 打开，但不要把 Member 一并打开。

### D3. 装配：profile 并集 + 共享集合

在 `_merge_file_catalog_skills`（或紧挨着的纯函数）中：

1. 仍按 profile + 请求 `skill_ids` + Project 裁剪得到基线。
2. 若本轮合格，把「已安装 ∧ 已启用 ∧ (`availableToAllAgents` ∨ id∈`ALWAYS_INJECTED_SKILL_IDS`)」且通过 Project 可见性的包并入 `used_skills`。
3. 去重保序：先 profile，再共享。

不改 Agent 文件、不回写 `skills:`。关掉开关后，profile 里仍列出的项继续有效。

### D4. 持久化两处，与 enabled 对齐

- 登记在 sqlite `skills` 的用户 / 系统内置行：`_ensure_column(..., "available_to_all_agents", "INTEGER NOT NULL DEFAULT 1")`，`replace_skills` 读写该键。
- 仅磁盘发现的外部包：扩展现有 `json_store` `external_skill_overrides`（已有 `enabled`），同文档加 `availableToAllAgents`。

API：`Skill` 响应带 `availableToAllAgents`；新增 `POST /api/skills/{id}/share` 或在 enable 请求体旁增加专用 PATCH。`yinli-catalog` 写 false 返回 409。

不把开关写入 `SKILL.md` frontmatter（避免改用户包正文）；文件仍只负责说明书。

### D5. 前端两个开关

Skills 详情在「启用中」旁加「所有 Agent 可用」。列表可用小标签标明共享。Agent 编辑器的技能选择器：共享项显示为已覆盖 / 可不再勾选；仍允许手动勾上，以便关闭共享后不丢。

不改 Chat SSE 契约。

## Risks / Trade-offs

- [用户把危险 Skill 打开共享] → 默认 true 便于办公包开箱即用；排除 Job / Worker；停用仍一刀切，要单人用再关开关。
- [共享目录变长、选错包] → 只加名称描述，正文按需；不预灌 SKILL.md。
- [Team Leader 与 Member 技能不一致] → 刻意：成员隔离，不把办公包偷偷带进私有目录。
- [旧客户端不展示开关] → 字段缺省 true，与升级列 DEFAULT 1 一致。

## Migration Plan

1. 加列与 override 键，旧数据升级为 true（`DEFAULT 1`）；`yinli-catalog` 代码锁定，不依赖列值。
2. 先部署后端装配与 API，再上前端开关。
3. 回滚：装配忽略该字段即回到「必须写进 Agent」；列可留。

## Open Questions

- 是否在首次进入 Skills 页时提示「可把 Word / 图表设为所有 Agent 可用」。不影响契约，实现时再定文案。
