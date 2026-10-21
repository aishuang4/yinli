# 文件 Catalog

> 状态：2026-10-09。路径和文件格式以本文为准。迁移步骤和分期实施清单已删除，历史在 git。

持久定义以文件为权威，不把用户正文再 upsert 进 sqlite。

## 路径

`USER_DATA_ROOT` 来自 `src.config.runtime_paths`。下面的路径都先 `Path.resolve()`。

```text
<USER_DATA_ROOT>/workspaces/<workspace_id>/.agents/
  profiles/<agent-id>.md
  mcp/<id>.yaml
  tools/<id>.yaml
  jobs/<id>.md

<USER_DATA_ROOT>/workspaces/_system/.agents/profiles/
  default.md
  system-websearch-job.md

<project_root>/.agents/skills/<name>/SKILL.md
<project_root>/.agents/INDEX.md
```

- `workspaces` 是复数。不要写成单数 `workspace/`（那是默认项目根）。
- `_system` 不是 SQLite 里的 Workspace，禁止插入 `workspaces` 表。
- 禁止把持久 Chat Agent 放进 `.agents/agents/` 或 `.agents/subagents/`。那是 Subagent 扫描路径。
- 禁止在 Project 里再放一份 profile 权威副本。
- 文件名必须是 `<id>.md` 或 `<id>.yaml`，与 frontmatter / 文档 `id` 一致。`id` 匹配 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`。
- UTF-8，无 BOM。单个 profile 上限 256 KiB。YAML 只用 `yaml.safe_load`，结果必须是 dict。

## Profile

`role` 只允许 `chat` 或 `job`。模型、采样和供应商不写进 profile，模型在 `models.json`（桌面）或平台目录（托管 Web）。`planConfig` 不写。`workspaceId` 由目录决定。

`role=job` 时必须有 `job_toolkit.required` / `optional`。Agent 档案不再保存工具审批档。系统 `default` 不能改成 Job Agent。`system-websearch-job` 不可删除，也不可改成 Chat Agent。

`filesystem_scope` 空字符串表示运行时用 Project root。文件里禁止写 `/`；写了按空字符串处理。

## Skill、MCP、HTTP、Job

- Skill 正文只来自 `SKILL.md`。项目 `.agents/skills` 免 Project binding。已启用且 `availableToAllAgents`（缺省 true）的包进入单 Agent、Sandbox 和 Team Leader 目录，不必写进 Agent `skills:`。关掉后仍须手动绑定。只有 `yinli-catalog` 锁定共享。Job、群聊 Worker / Host、Team Member、无工具基础设施 Agent 不因该开关自动拿 Skill。
- MCP / HTTP 定义是 YAML。密钥只写 `secret:` 引用，不把 token 写进文件。
- Job 定义是 `.agents/jobs/<id>.md`。`scheduled_jobs` 表只保留运行实例（状态、下次时间、失败次数、能力合同）。交互式单 Agent Chat 用 `ScheduleJob` / `ListJobs` / `PauseJob` / `ConfirmJob` / `CheckJobFeasibility` 登记，写库时同步落文件。Team 与 firing 不注入这组工具。禁止再造一套系统 CRUD 工具。

Chat 会生成项目 `.agents/INDEX.md`。`get_registered_agent` 读 catalog，不读 sqlite 里的用户正文。
