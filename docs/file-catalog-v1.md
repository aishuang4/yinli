# 文件 Catalog

> 状态：2026-10-09。路径和文件格式以本文为准。迁移步骤和分期实施清单已删除，历史在 git。

持久定义以文件为权威，不把用户正文再 upsert 进 sqlite。全局 catalog 在 tenant root。Session 文件在 session root。外部目录只作为 working directory，Run 产物在 Run workspace。不再把 INDEX 或 `.agent-workstation` 写进外部目录。

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

<USER_DATA_ROOT>/skills/<name>/SKILL.md
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

`role=job` 时必须有 `job_toolkit.required` / `optional`。加载后强制 `human_approval=false`、`tool_permission_profile=trusted_tools`。系统 `default` 不能改成 Job Agent。`system-websearch-job` 不可删除，也不可改成 Chat Agent。

`filesystem_scope` 空字符串表示运行时用 Project root。文件里禁止写 `/`；写了按空字符串处理。

## Skill、MCP、HTTP、Job

- Skill 正文只来自 `<USER_DATA_ROOT>/skills/<name>/SKILL.md`。注册、导入、自进化和迁移补包都写入该统一目录；项目、Workspace 和 HOME 下的兼容 Skill 目录不参与管理或运行时扫描。已启用且 `availableToAllAgents`（缺省 true）的包进入单 Agent、Sandbox 和 Team Leader 目录，不必写进 Agent `skills:`。关掉后仍须手动绑定。只有 `yinli-catalog` 锁定共享。Job、群聊 Worker / Host、Team Member、无工具基础设施 Agent 不因该开关自动拿 Skill。
- MCP / HTTP 定义是 YAML。密钥只写 `secret:` 引用，不把 token 写进文件。
- Job 定义是 `.agents/jobs/<id>.md`。`scheduled_jobs` 表只保留运行实例（状态、下次时间、失败次数、能力合同）。交互式单 Agent Chat 用 `ScheduleJob` / `ListJobs` / `PauseJob` / `ConfirmJob` / `CheckJobFeasibility` 登记，写库时同步落文件。Team 与 firing 不注入这组工具。禁止再造一套系统 CRUD 工具。

Chat 会生成项目 `.agents/INDEX.md`。`get_registered_agent` 读 catalog，不读 sqlite 里的用户正文。


## Skill 文件加载（2026-10）

公开入口为 `src.skills.build_skill_catalog` / `resolve_skill` / `read_skill`。
包是否存在、名称、描述、正文和资源完全来自磁盘；SQLite `skills`、旧 JSON 和
`external_skill_overrides` 只供一次性迁移，不再作为读取门禁或运行时双写目标。
列表不携带正文，详情、Composer 显式预加载和 SDK viewer 使用同一 reader。

唯一默认来源是 `<tenant>/skills`。系统 seed、用户注册、压缩包导入、自进化注册和历史正文迁移都落在这里。Project root、进程 cwd、Workspace catalog 和 HOME 不改变 Skill 搜索根，也不能产生第二个同名候选。

扫描有界递归，遇到 `SKILL.md` 停止下钻，排除依赖/支持目录与越界符号链接。
同一个真实目录去重；不同目录即使说明书相同也不合并。统一 UTF-8（解析允许 BOM）、safe YAML，
缺 name 用目录名，缺 description 用正文首个非标题文本；无可用描述/编码错误/YAML 错误返回 invalid。

非系统 ID 为 `skill-` 加规范 JSON `[ownerKey, rootKey, relativePackagePath]` SHA-256 前 24 hex。
正文编辑不改变身份，目录改名会改变身份，不能按同名接管旧授权。原始 `SKILL.md` 全 SHA-256 单独表示版本。
统一根内同名包报 `skill_ambiguous`，通过 `<rootKey>:<relativePackagePath>` 或 ID 精确选择。系统保留名称不可被普通包冒充。
SDK 接收唯一 loadName，避免 LocalSkillLoader 二次解析和同名后写覆盖。

策略位于 `<tenant>/workspaces/<workspace>/.agents/skills-policy.json`，schemaVersion 为 1，
version 随保存增加。配置仅记录根、以稳定 ID 为键的 enabled / availableToAllAgents / installed、
有界旧 ID 别名、legacyOwnership 与迁移诊断，不复制名称/描述/正文目录。策略不存在用来源默认值，
策略损坏报 `skill_policy_invalid`，不恢复成全启用。保存锁住跨线程/进程的 read-modify-write，原子替换并检查版本。
新 Run/刷新检查目录成员和文件签名，快照不可变；读取时变更报 `skill_catalog_stale`。

统一目录中的普通包遵守 Project binding；共享开关只绕过 Agent profile 绑定，不绕过 Project 或租户边界。Team Member/Subagent 只解析显式授予的引用，
Job/群聊/无工具 Agent 不因共享自动增加 Skill。Docker 按稳定 ID 投影，避免相同目录名覆盖。
已激活正文按稳定 ID、source、hash 恢复；下一轮撤销失效指令，仅替换受控 Skill 工具块/HintBlock，保留用户原文与历史审计。

## Skill 迁移与回滚

启动阶段先 seed 系统包，再执行 `src.skills.migration.migrate_tenant_skills`。查询不得 seed 或补包。
seed 保存内容签名，不重复改 mtime；可编辑包被用户修改时保留原文件，升级候选存入隐藏 `.upgrades`。
`yinli-catalog` 作为锁定系统说明书在启动/升级时受控更新。

迁移记录在 `<tenant>/data/skill-file-migration-v1/`：`backup/` 保存旧表/overrides/受管包/profiles/
Job 定义及 SQLite 一致性快照，`report.json` 提供逐条映射，`complete.json` 仅在文件和策略 round-trip 后写入。
已有文件优先；只有受管、owner 明确的合法 body-only 历史记录允许在迁移阶段补包。
旧 ID 按 owner 与路径映射，重复旧 hash 不猜测；冻结 Job 来源路径可作为唯一证据，历史 hash 不改。
无文件、来源不明或歧义引用保留内部诊断，但不伪装成可安装 Skill。Skill 资源变更不反向扫描或暂停 Job；Job 在 confirm、resume 和 firing 前按冻结清单失败关闭并要求重新确认。正常 reader 不查询旧表，也不重建丢失文件。

回滚必须停止服务后使用同一批备份与旧部署。`restore_skill_backup` 在恢复前把新模式全部 Skill/
Workspace 文件和可选数据库导出到 `before-rollback-*`，避免旧代码覆盖新建包与策略。
不得只恢复一个旧登记行就启动旧版本，也不得同时维护两套 live registry。
