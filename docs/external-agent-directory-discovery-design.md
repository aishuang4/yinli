# 外部 Agent 目录发现与运行时兼容方案

> 状态：方案草案，待确认后实现。
>
> 目标：直接发现项目和用户级 `.agents`、`.agent`、`.claude` 目录中的 Skills、Subagents 定义，使其进入 YINLI catalog 并参与当前 ReAct / DelegateTask 运行；不复制外部文件，不恢复新的 Runtime 类型。

## 1. 设计结论

外部目录作为定义文件的权威来源。YINLI 只负责扫描、解析、合并、权限裁剪、运行时路径转换和审计。

```text
项目 / 用户外部目录
        -> Discovery
        -> 统一 Skill / Subagent catalog
        -> Workspace / Project / Agent 权限裁剪
        -> 当前 ReAct、DelegateTask、AgentScope
```

不把外部文件复制到 `skills` 或 `subagents`。SQLite 不保存外部正文，只保存可选的禁用状态、解析诊断、hash 和使用审计。

## 2. 支持范围

### 2.1 项目级目录

以当前 Project root 为基准，支持：

```text
<project_root>/.agents/skills/*/SKILL.md
<project_root>/.agents/agents/**/*.md
<project_root>/.agents/subagents/**/*.md

<project_root>/.agent/skills/*/SKILL.md
<project_root>/.agent/agents/**/*.md
<project_root>/.agent/subagents/**/*.md

<project_root>/.claude/skills/*/SKILL.md
<project_root>/.claude/agents/**/*.md
```

`.agents/subagents` 是当前已有格式，继续兼容。`.agent` 和 `.claude` 是新增兼容来源。

### 2.2 用户级目录

用户级目录包含应用运行时数据根和操作系统用户主目录下的固定标准目录，不扫描任意 home 子目录：

```text
<user_data_root>/.agents/skills/*/SKILL.md
<user_data_root>/.agents/agents/**/*.md
<user_data_root>/.agents/subagents/**/*.md

<user_data_root>/.agent/skills/*/SKILL.md
<user_data_root>/.agent/agents/**/*.md
<user_data_root>/.agent/subagents/**/*.md

<user_data_root>/.claude/skills/*/SKILL.md
<user_data_root>/.claude/agents/**/*.md
```

同时扫描真实用户主目录中的标准共享目录：

```text
~/.agents/skills/*/SKILL.md
~/.agents/agents/**/*.md
~/.agents/subagents/**/*.md

~/.agent/skills/*/SKILL.md
~/.agent/agents/**/*.md
~/.agent/subagents/**/*.md

~/.claude/skills/*/SKILL.md
~/.claude/agents/**/*.md
```

这些目录属于用户级来源，默认可见但不可直接运行，仍需显式启用；运行时只接受固定目录下的文件，并继续受当前 Agent 的工具、文件和审批权限约束。

## 3. 统一 catalog

### 3.1 Skill

现有 `Skill` 增加外部来源元数据，保持现有运行时字段兼容：

```text
id                  稳定内部 ID，外部定义使用 source + 相对路径派生
name                frontmatter name 或目录名
description         frontmatter description
content             SKILL.md 正文
resource_path       Skill 包目录
resources           包内相对资源列表
source_kind         native | external_agents | external_agent | external_claude
source_scope        project | user
source_path         原始 SKILL.md 绝对路径
content_hash        内容 hash
compatibility_warnings 兼容诊断
```

外部 Skill 的 `id` 不直接使用裸名称，避免与 SQLite Skill 或不同项目同名冲突。运行时引用支持 `id`、名称和明确的来源路径别名，但页面和审计始终显示实际 `source_path`。

### 3.2 Subagent

复用当前 `SubagentDefinition`，增加来源字段：

```text
source_kind
source_path
content_hash
compatibility_warnings
```

现有字段 `name`、`description`、`tools`、`disallowedTools`、`model`、`permissionMode`、`maxTurns`、`skills` 继续归一化。`hooks`、`memory`、`isolation`、inline MCP 等暂不执行，只记录 warning。

## 4. 优先级和冲突

同一 scope 内按来源顺序处理：

```text
.claude > .agents > .agent > 原生目录
```

跨 scope 的优先级：

```text
项目级 > 用户级 > YINLI 原生用户定义 > 系统内置定义
```

同名定义不静默合并：

- 高优先级定义进入 active catalog。
- 低优先级定义保留为 shadowed 记录。
- catalog API 返回覆盖关系、来源和 warning。
- 运行审计记录 active 定义的 `source_path`、`content_hash` 和来源类型。

Skill 与 Subagent 分别按各自命名空间处理，不因同名互相覆盖。

## 5. `resolve_runtime_skill_paths()` 路径方案

可以增加固定路径，但固定的应是“受信任根目录集合”，不能把 resolver 改成接受任意 Skill 路径。

### 5.1 受信任根目录

每次 Runtime 根据 `RuntimeContext` 计算不可变 allowlist：

```text
1. <project_root>/.agents/skills
2. <project_root>/.agent/skills
3. <project_root>/.claude/skills
4. <user_data_root>/.agents/skills
5. <user_data_root>/.agent/skills
6. <user_data_root>/.claude/skills
7. <user_data_root>/skills             # 现有原生 Skill 包
```

首期不允许调用方传入任意 `allowed_roots` 覆盖集合；由统一 helper 根据 `RuntimeContext` 和运行模式派生，避免不同入口产生不同安全边界。

### 5.2 resolver 行为

建议将接口扩展为：

```python
resolve_runtime_skill_paths(
    skills,
    *,
    runtime_context: RuntimeContext | None = None,
) -> list[str]
```

内部流程：

1. 对每个 `resource_path` 做 `resolve()` / `realpath()`。
2. 要求 Skill 包目录包含 `SKILL.md`。
3. 要求包目录位于固定受信任根目录之一的子路径内。
4. 拒绝越界 `..`、绝对路径伪造和指向根目录外的符号链接。
5. 检查包内资源不能通过符号链接越界。
6. 去重后按稳定顺序返回绝对路径。
7. 返回的路径只用于 AgentScope `skills_or_loaders`，不改变普通 Read / Bash 的权限范围。

对于没有 `runtime_context` 的纯单元测试或基础工具调用，只允许 `<user_data_root>/skills` 根目录，不自动放开项目外部目录。

### 5.3 Project / Agent 裁剪

外部 Skill 被发现不等于可用。最终可用集合仍为：

```text
发现目录
∩ Agent 配置 Skills
∩ Project enabled binding
∩ Runtime filesystem scope
∩ 当前运行模式允许范围
```

如果外部 Skill 没有 SQLite binding，首期采用“发现可见、默认不可运行”，用户显式启用后才进入 active catalog。若产品决定标准目录天然视为项目资源，则必须记录这一规则，并只对当前 Project root 下的目录生效，用户级目录仍需显式绑定。

## 6. 注入链路

### 6.1 Chat / 单 Agent

```text
RuntimeContext
  -> discover_external_skills(project_root, user_data_root)
  -> merge native + external catalog
  -> select_runtime_skills()
  -> resolve_runtime_skill_paths(runtime_context=...)
  -> factory.skills_or_loaders
```

### 6.2 Subagent

```text
SubagentDefinition.skills
  -> resolve by id / name / external alias
  -> Project + parent tool scope 裁剪
  -> resolve_runtime_skill_paths(runtime_context=...)
  -> child AgentScope runtime
```

`DelegateTask`、Team Member 和主 Agent 不新增外部目录专用工具；只复用现有 Skill catalog 和路径转换。

## 7. 刷新、缓存和删除

- 启动时加载一次 catalog。
- Skills / Subagents 页面提供手动刷新。
- Runtime 启动前按目录列表、文件路径、mtime 和 content hash 生成 discovery fingerprint。
- 外部 `SKILL.md` 或 Subagent 文件变更后，相关 Agent runtime cache 自动失效。
- 文件删除或目录移除后，下次刷新从 active catalog 移除。
- 已运行的 Run 保持本轮解析快照，不因中途文件变化改变当前行为。
- 解析失败不阻塞 Chat，进入 catalog errors / warnings。

## 8. Docker 和文件系统

### 首期

- LocalWorkspace 支持固定根目录下的外部 Skill。
- 持久 DockerWorkspace 将被允许的外部 Skill seed 到 `/workspace/skills/<source-id>`。
- catalog 中向 AgentScope 暴露容器内路径，不暴露宿主绝对路径。
- 非持久 Docker + Skills 继续明确拒绝，沿用现有约束。

### 不做的事情

- 不把整个 `.claude` 或 `.agents` 目录复制进容器。
- 不允许外部 Skill 修改项目根外文件。
- 不允许 Skill 通过脚本自行扩大 Bash、网络或文件权限。

## 9. 前端和审计

Skills / Subagents 页面增加：

- 来源类型：原生、`.agents`、`.agent`、`.claude`。
- 来源 scope：项目级、用户级。
- 实际文件路径的相对展示。
- active / shadowed / disabled 状态。
- 解析失败和不支持字段 warning。
- 最近 hash / 修改时间。

Runtime 审计至少记录：

```text
sourceKind
sourceScope
sourcePath
contentHash
loadedSkills
missingSkillRefs
compatibilityWarnings
```

## 10. 实现顺序

### Phase 1：Subagent 外部目录发现

- 扩展 `subagents/registry.py` 的目录集合。
- 支持 `.agent`、`.claude` 和 `.agents/agents`。
- 增加来源、覆盖和 warning。
- 不改变 DelegateTask 运行协议。

### Phase 2：Skill 外部目录发现

- 抽出统一 Skill discovery loader。
- 支持三个目录来源的 `SKILL.md`。
- 扩展 `Skill` 来源元数据和 catalog 合并。
- 修改 `resolve_runtime_skill_paths()` 使用固定受信任根目录。

### Phase 3：Docker、UI 和持久化诊断

- 持久 Docker seed。
- 页面展示来源、冲突和 warning。
- 保存用户禁用状态和解析诊断，不保存外部正文。

## 11. 验收测试矩阵

至少覆盖：

- 三种项目目录和三种用户目录的发现。
- `.agents/subagents` 现有行为不回归。
- Claude frontmatter 字段归一化。
- 项目级覆盖用户级、同 scope 重名和 shadowed 展示。
- Skill 外部路径进入 AgentScope，包外路径被拒绝。
- `../`、绝对路径和越界 symlink 被拒绝。
- Project binding、Agent 配置和父 Subagent 工具上限继续生效。
- 文件修改、删除和手动刷新后的 catalog / cache 行为。
- LocalWorkspace 路径可用，持久 Docker 路径正确 seed。
- 运行审计包含来源、hash 和 warning。

## 12. 待确认事项

1. 项目级外部 Skill 是否默认可运行，还是必须先绑定 Project。
2. 是否首期扫描真实用户 home 下的 `~/.claude`、`~/.agents`、`~/.agent`。
3. `.claude/commands`、`.claude/hooks`、plugin manifest 是否纳入本需求；当前建议不纳入。
4. 同名优先级是否确认 `.claude > .agents > .agent > 原生`。
5. 是否接受首期只支持 LocalWorkspace，持久 Docker 在 Phase 3 接入。
