# AgentScope 原生 Skills 第一版改造设计

> 状态：第一版已实现；DeepSeek 本地行为验证通过，第二供应商验证因 DashScope Key 无效暂未完成。本文定义 Agent Workstation 从“应用层全文注入 Skill”迁移到 AgentScope 2.0 原生 Skills 的第一版方案。

## 1. 背景

当前项目虽然已经支持 Skill 注册、zip 导入、SQLite 持久化、Workspace / Project binding、Agent 绑定、运行状态和 Skill 自进化，但 Chat Runtime 并未使用 AgentScope 原生 Skills。

当前链路为：

```text
应用层 Skill registry
  -> 规则关键词选择本轮 used_skills
  -> build_skill_system_prompt_from_skills() 注入完整 SKILL.md
  -> AgentScope 只把 Skill 当作普通 system prompt
```

当前实现存在以下问题：

1. Skill 正文在模型调用前整体进入 system prompt，不符合渐进式披露原则。
2. AgentScope 不知道当前有哪些 Skill，内置 `Skill` viewer 工具没有启用。
3. 应用层关键词选择器提前决定 Skill，模型无法根据完整语义自主选择。
4. `scripts/`、`references/`、`assets/` 虽然会被 zip 导入器解压，但缺少统一运行时说明。
5. Skill 是否真正被模型加载，与当前 `SKILL_STARTED` / `SKILL_COMPLETED` 生命周期不一致。
6. Skill 正文同时承担管理数据和运行时上下文，后续难以对接标准 Agent Skills 目录模型。

AgentScope 2.0.3 已提供以下原生能力：

- `agentscope.skill.LocalSkillLoader`：扫描 Skill 目录并读取 `SKILL.md`。
- `Toolkit.skills_or_loaders`：向 Toolkit 注册 Skill 或 Skill loader。
- 内置 `Skill` viewer 工具：模型按 Skill 名称读取完整正文。
- `Toolkit.get_skill_instructions()`：只把 Skill 名称、描述和目录加入系统提示。
- 基于 `SKILL.md` 修改时间的 loader 缓存。

第一版应复用这些能力，不再维护一套重复的 Skill prompt 注入机制。

## 2. 外部规范依据

第一版遵循 Agent Skills 开放规范的核心约定：

```text
skill-name/
  SKILL.md
  scripts/       可选，可执行脚本
  references/    可选，按需参考资料
  assets/        可选，模板和静态资源
```

固定语义：

1. 会话开始只披露 Skill `name` 和 `description`。
2. 模型决定使用 Skill 后再加载完整 `SKILL.md`。
3. `scripts/`、`references/` 和 `assets/` 只在 Skill 正文引用时按需访问。
4. Skill 正文中的文件路径以 Skill 根目录为基准，例如 `scripts/inspect.py`。
5. Skill 脚本复用通用 Shell 工具，不为每个脚本创建专用 Tool。
6. 支持的脚本语言、沙箱和权限由 Agent 宿主决定，不属于 Skill 包格式本身。

参考资料：

- `https://agentskills.io/specification`
- `https://agentskills.io/client-implementation/adding-skills-support`
- `https://agentskills.io/skill-creation/using-scripts`

## 3. 第一版目标

第一版必须实现：

1. 将当前 Project 和 Agent 允许使用的 Skill 包目录注册到 AgentScope `Toolkit.skills_or_loaders`。
2. 使用 AgentScope 内置单一 `Skill` viewer 工具加载 Skill 正文。
3. 删除 Chat Runtime 对完整 Skill 正文的预注入。
4. 保留现有 Skill 安装、SQLite、Workspace / Project binding、Agent 配置和 Skill 自进化能力。
5. Skill 正文继续使用 `scripts/foo.py`、`references/bar.md` 等相对路径。
6. 模型调用 Read、Grep、Bash 等工具时，根据 AgentScope catalog 中的 Skill `dir` 解析为绝对路径。
7. Skill 脚本继续通过现有通用 `GuardedBash` 执行，不新增 `skill_script_run`，不为每个脚本注册工具。
8. Skill 生命周期改为以模型实际调用 AgentScope `Skill` 工具为准。
9. 单 Agent、Team 临时 Member 和 `DelegateTask` Subagent 使用同一套 Skill 注册规则。
10. Skill 目录访问继续受 RuntimeContext、Project binding、Agent 配置和权限策略共同约束。

## 4. 第一版非目标

第一版不实现：

- 不修改 AgentScope 源码。
- 不实现 Shell 命令字符串重写器。
- 不自动切换 Bash 的当前工作目录。
- 不新增每脚本 Tool 或每 Office 操作 Tool。
- 不实现 Claude Code 的 `${CLAUDE_SKILL_DIR}`、动态 Shell 注入或私有 frontmatter 扩展。
- 不完整实现 `allowed-tools` 的跨客户端兼容语义。
- 不在运行时自动安装任意 Skill 依赖。
- 不实现 Skill marketplace、签名、远程仓库安装或自动更新。
- 不直接复制或分发第三方产品的受限 Skill 内容。
- 不恢复旧独立 Plan Runtime、Subagent Runtime 或新增 `skill` Runtime 分支。

## 5. 总体架构

```text
SQLite skills + Skill package directories
  -> Workspace / Project resource binding
  -> Agent configured skill ids
  -> resolve_runtime_skill_paths()
  -> MainAgentRuntimeSpec.skill_paths
  -> agents.factory._create_default_toolkit()
  -> Toolkit(skills_or_loaders=skill_paths)
  -> AgentScope catalog: name + description + dir
  -> model calls Skill({"skill": "exact-name"})
  -> AgentScope returns SKILL.md body
  -> model follows instructions
       -> Read/Grep references/*
       -> Bash scripts/*
       -> existing MCP / HTTP / filesystem tools
```

职责边界：

### 应用层负责

- Skill 注册、导入、启用、删除和持久化。
- Skill 包目录的安全解压和资源索引。
- Workspace / Project ownership 和 resource binding。
- Agent 允许使用哪些 Skill。
- Skill 自进化候选和注册流程。
- 权限、HITL、Trace、RunState 和前端展示。
- 将允许使用的 Skill 路径传给 AgentScope。

### AgentScope 负责

- 从 Skill 目录加载 `SKILL.md`。
- 生成轻量 Skill catalog。
- 提供统一 `Skill` viewer 工具。
- 按名称返回 Skill 正文。
- 缓存 Skill 文件解析结果。

### Skill 包负责

- 在 `SKILL.md` 中描述使用时机、步骤、约束和验证方式。
- 使用相对 Skill 根目录的路径引用 `scripts/`、`references/` 和 `assets/`。
- 脚本提供非交互参数、清晰错误、稳定退出码和可控输出。

### 通用工具负责

- `Read` / `Grep` / `Glob` 读取参考资料和项目文件。
- `GuardedBash` 执行 Skill 脚本和普通项目命令。
- MCP / HTTP Tool 执行外部系统操作。
- `AskUser` 处理业务信息缺口。

## 6. Skill 可见范围

第一版不再由关键词选择器提前确定 `used_skills`。传给 AgentScope 的 Skill 集合按以下顺序机械裁剪：

```text
Workspace 已安装且启用的 Skills
  ∩ 当前 Project resource binding
  ∩ 当前 Agent 配置的 skill ids
  ∩ 当前请求显式 skillIds（如果请求提供）
```

结果定义为 `available_runtime_skills`。AgentScope 只接收这些 Skill 的包目录。

不满足任一约束的 Skill：

- 不出现在 AgentScope catalog。
- 不能通过内置 `Skill` 工具加载。
- 不能通过猜测名称或路径绕过 Project binding。

模型负责从 catalog 中自主判断哪个 Skill 与当前任务匹配。第一版停止使用 `select_relevant_skills()` 的关键词评分作为主运行时选择机制，该函数只在兼容迁移或非 Chat 管理入口中临时保留。

## 7. 路径语义

### 7.1 Skill 作者看到的路径

Skill 正文使用标准相对路径：

````markdown
先读取 `references/formulas.md`。

然后运行：

```bash
python scripts/inspect_workbook.py /absolute/project/input.xlsx
```
````

Skill 正文不写：

```text
$SKILL_DIR
${CLAUDE_SKILL_DIR}
<path-to-skill>
skill://...
```

### 7.2 模型运行时路径

AgentScope catalog 已包含每个 Skill 的绝对 `dir`。第一版通过自定义 `skill_instruction_template` 向模型提供统一规则：

```text
Skill 中的相对文件路径以该 Skill 的 dir 为基准。
调用 Read、Grep、Glob 或 Bash 时，先将 Skill 相对路径转换为该 dir 下的绝对路径。
项目文件仍以当前 Project workspace 为基准。
不要猜测 Skill 目录，不要扫描未注册的 Skill。
```

示例：

```text
catalog dir:
  /data/skill_packages/excel-report

SKILL.md:
  python scripts/inspect_workbook.py /project/input.xlsx

实际 Bash tool call:
  python /data/skill_packages/excel-report/scripts/inspect_workbook.py /project/input.xlsx
```

### 7.3 第一版不做内部路径改写的原因

通用 Bash 接受完整 Shell 命令字符串，可能包含管道、重定向、条件运算和引号。对命令做正则替换或不完整 `shlex` 重写会引入错误和命令注入风险。

第一版采用 Agent Skills 宿主实现指南中的文件读取模式约定：宿主披露 Skill 根目录并明确解析规则，由模型在工具调用中提交最终绝对路径。后续只有在真实评测证明路径错误率不可接受时，才评估结构化命令执行器或 Skill-aware workspace。

## 8. AgentScope 接入设计

### 8.1 Runtime 类型

`backend/src/server/routes/chat/runtime_builder.py` 的 `MainAgentRuntimeSpec` 增加：

```python
skill_paths: list[str]
```

`skill_paths` 必须：

- 只包含绝对目录路径。
- 每个目录都包含 `SKILL.md`。
- 已通过当前 RuntimeContext 和 Project binding 裁剪。
- 顺序稳定，用于 Agent cache key。

Agent cache key 增加 Skill 路径和 `SKILL.md` 修改时间摘要，保证 Skill 内容更新后旧 Agent cache 失效。

### 8.2 Agent 工厂

`backend/src/agents/factory.py` 的以下函数增加 `skill_paths` 参数：

- `_create_default_toolkit()`
- `get_or_create_agent()`

Toolkit 构造改为：

```python
Toolkit(
    tools=local_tools,
    skills_or_loaders=skill_paths,
    mcps=mcp_clients,
    skill_instruction_template=AGENT_WORKSTATION_SKILL_INSTRUCTION,
)
```

`AGENT_WORKSTATION_SKILL_INSTRUCTION` 基于 AgentScope 默认模板，只增加项目需要的路径、权限和禁止扫描说明，不复制完整 Skill 正文。

### 8.3 移除全文预注入

`backend/src/server/routes/chat/run_context.py` 不再把以下内容加入 `system_prompt`：

```python
build_skill_system_prompt_from_skills(used_skills)
```

以下内容继续保留：

- Project context。
- Runtime context。
- IntentRoute instruction。
- Conversation history。
- SkillRun 恢复状态摘要。
- Memory injection。
- clarification 和 final answer 规则。

`build_skill_system_prompt_from_skills()` 暂时保留给历史测试、Subagent 迁移和非 AgentScope 基础设施调用；全部调用点迁移完成后再删除，不在第一步直接移除公共函数。

## 9. 通用 Bash 和 Skill 脚本

第一版保持一个通用 `GuardedBash`，不增加模型可见工具数量。

需要修正当前工具注入边界：Bash 是本地执行能力，不应放在 `permission_profile.can_use_network` 分支内。目标结构为：

```python
if scope.allow_bash and permission_profile.can_execute:
    local_tools.append(GuardedBash(...))

if permission_profile.can_use_network:
    # HttpRequest / WebFetch / MCP / 网络知识库
```

`UnifiedPermissionProfile` 增加或派生 `can_execute`，保持：

- `read_only` 禁止 Bash。
- `trusted_tools` 可直接运行允许的本地命令。
- `confirm_destructive` 对写入、执行和破坏性命令进入确认。
- `ask_every_time` 每次执行都确认。

第一版 `allow_bash` 仍受 Agent 配置和 RuntimeToolScope 限制。Skill 存在 `scripts/` 不自动突破 Agent 的 Bash 能力上限；如果 Agent 禁止 Bash，模型仍可读取 Skill，但必须报告脚本执行能力不可用，不能降级到未授权执行路径。

## 10. Skill 生命周期与事件

当前实现会在模型运行前根据应用层选择结果发送 Skill started，并在 `REPLY_END` 时统一完成。迁移后必须以 AgentScope `Skill` 工具调用为事实源。

目标映射：

```text
TOOL_CALL_START name=Skill
  -> SKILL_STARTED

TOOL_RESULT_END name=Skill, success
  -> SKILL_CONTEXT
  -> SkillRun status=active

TOOL_RESULT_END name=Skill, failure
  -> SKILL_FAILED

RUN_COMPLETED 且 SkillRun active
  -> SKILL_COMPLETED

AskUser 暂停
  -> SkillRun status=waiting_user
```

`Skill` viewer 仍可作为普通工具进入通用工具历史，但前端应优先把它投影为 Skill 生命周期，不重复显示一条无意义的普通工具卡片。

第一版优先复用现有稳定事件名，不新增事件家族。由于 `SKILL_STARTED` 的触发语义发生变化，实施前必须先更新 `docs/chat-sse-event-contract.md`。

## 11. 单 Agent、Team 和 Subagent

### 单 Agent

主 Agent 接收 `available_runtime_skills` 的全部包目录，模型按需调用 AgentScope `Skill`。

### Team Leader

Leader 的 Skill 范围与其 Agent 配置和 Project binding 一致。Team 控制阶段如果保持仅 Team 控制工具，则不得额外开放业务 Skill；进入实际业务执行的 Member 才加载适用 Skill。

### 临时 Member

Member 继承 Leader 可用能力的裁剪结果，但不能突破：

- 当前 Project binding。
- Member 工具 allowlist / denylist。
- Team Runtime 的恢复轮工具限制。

恢复轮继续执行：

```python
group.skills_or_loaders = []
```

保证恢复轮只提交已有结果，不重新加载 Skill 或扩散任务。

### DelegateTask Subagent

Subagent frontmatter 中的 `skills` 只解析已安装、启用且当前 Project 允许的 Skill。迁移后不再把完整 Skill prompt 拼入 Subagent system prompt，而是把对应包路径传给 Subagent Toolkit。

## 12. Skill 包管理

当前 zip 导入器已经能保留 Skill 包中的任意安全文件，第一版继续复用：

- 最大压缩包大小。
- 最大文件数量。
- zip 路径穿越防护。
- `SKILL.md` UTF-8 校验。
- Workspace ownership。

第一版增加安装诊断，但暂不强制迁移全部历史 Skill：

1. 缺少 `SKILL.md`：拒绝安装。
2. 缺少 `name` 或 `description`：拒绝安装。
3. `name` 与目录名不一致：记录 warning，允许安装。
4. 名称不符合 Agent Skills 小写连字符规范：记录 warning，允许历史 Skill 继续使用。
5. `scripts/` 存在不可识别文件类型：只列为资源，不自动执行。
6. 符号链接和越界路径：拒绝安装。

后续版本再决定是否对新 Skill 强制严格规范，并为历史中文 Skill 引入稳定 `displayName` 与标准 `name` 双字段。

## 13. 文件级改造范围

### 后端

- `backend/src/server/routes/skills.py`
  - 提供从持久 Skill 到安全包目录的解析函数。
  - 增加 AgentScope loader 兼容诊断。
  - 不再承担 Chat Runtime 全文注入主路径。
- `backend/src/server/routes/chat/run_context.py`
  - 构建 `available_runtime_skills`。
  - 移除完整 Skill 正文预注入。
- `backend/src/server/routes/chat/runtime_builder.py`
  - `MainAgentRuntimeSpec` 增加 `skill_paths`。
  - Agent cache key 增加 Skill 路径和更新时间摘要。
- `backend/src/agents/factory.py`
  - Toolkit 接入 `skills_or_loaders`。
  - 增加项目级 `skill_instruction_template`。
  - Bash 注入与网络权限解耦。
- `backend/src/server/routes/chat/leader_stream.py`
  - 将 AgentScope `Skill` 工具事件投影为 Skill 生命周期。
- `backend/src/server/routes/chat/initial_events.py`
  - 不再基于规则预选结果提前发送 `SKILL_STARTED`。
- `backend/src/subagents/runtime_context.py`
  - 从 prompt 注入迁移为 Skill 包路径传递。
- `backend/src/subagents/runner.py`
  - 将裁剪后的 Skill 路径交给 Subagent Toolkit。
- `backend/src/server/routes/chat/team_runtime/member_runtime.py`
  - 给 Member 传递裁剪后的 Skill loader。
  - 保持恢复轮清空 `skills_or_loaders`。
- `backend/src/security/permissions.py`
  - 明确本地 execute 与 network 权限边界。

### 契约和文档

- `docs/chat-sse-event-contract.md`
  - 更新 Skill 生命周期触发语义。
- `docs/architecture-overview.md`
  - 实现完成后更新为 AgentScope 原生 Skills 在线架构。
- `docs/project-progress-handoff.md`
  - 实现完成后记录验证结果和已知限制。
- `docs/project-todo.md`
  - 只维护实施任务状态，不复制本文设计。

### 前端

第一版不新增 Skill 页面主功能，只调整运行展示：

- Skill 只有在 AgentScope `Skill` 工具实际调用后才显示为运行中。
- `Skill` viewer 不重复显示为普通工具卡片。
- 详情继续展示 Skill 名称、描述、资源列表和状态。
- 安装详情增加 warning 展示时，复用现有 Skill 详情区域，不新建独立管理页面。

## 14. 实施顺序

严格按以下顺序推进：

1. 更新 `docs/chat-sse-event-contract.md` 的 Skill 生命周期语义。
2. 为 `MainAgentRuntimeSpec`、Agent cache key 和 factory 增加 `skill_paths`。
3. Toolkit 接入 AgentScope `skills_or_loaders` 和自定义 Skill instruction template。
4. 单 Agent 移除完整 Skill 正文预注入。
5. 将 AgentScope `Skill` 工具调用接入 SkillRun、Trace 和 SSE。
6. 迁移 `DelegateTask` Subagent 的 Skill 注入。
7. 迁移 Team 临时 Member 的 Skill 注入并验证恢复轮限制。
8. 将 Bash 本地执行权限与网络权限解耦。
9. 增加安装诊断和前端 warning 展示。
10. 更新架构、交接和 TODO 文档。

## 15. 测试计划

### AgentScope loader 单元测试

- 包目录包含标准 `SKILL.md` 时可被 `LocalSkillLoader` 读取。
- 缺少 `name` 或 `description` 时安装阶段给出明确错误。
- 修改 `SKILL.md` 后 Agent cache key 变化。
- Project 未绑定的 Skill 不进入 Toolkit。
- Agent 未配置的 Skill 不进入 Toolkit。

### Toolkit 测试

- 有 Skill 时工具 schema 只增加一个 `Skill` viewer。
- 多个 Skill 不产生多个模型工具。
- 无 Skill 时不出现 `Skill` viewer 和空 catalog。
- `get_skill_instructions()` 只包含名称、描述和目录，不包含完整正文。
- 调用 `Skill` 后返回对应 Markdown 正文。

### Chat Runtime 回归测试

- 运行前不再发送猜测性的 `SKILL_STARTED`。
- 模型调用 `Skill` 时产生 `SKILL_STARTED` 和 `SKILL_CONTEXT`。
- Skill 名称不存在时产生 `SKILL_FAILED`。
- Skill 使用后 `RUN_COMPLETED` 正确收口 `SKILL_COMPLETED`。
- AskUser 暂停和恢复保持 SkillRun 状态。
- 刷新后 RunState 能恢复 Skill 调用状态。

### 路径行为测试

- catalog 中提供绝对 Skill `dir`。
- Skill 正文保留 `scripts/foo.py` 相对写法。
- 模型调用 Bash 时使用 `dir/scripts/foo.py` 的绝对路径。
- Skill 脚本可以读取显式传入的 Project 文件绝对路径。
- 不允许访问未注册 Skill 目录。
- `../` 不得突破 Skill package 或 Project filesystem scope。

路径解析涉及模型行为，除单元测试外应增加 Sandbox 行为用例，分别使用至少两个模型供应商验证。

### Team / Subagent 测试

- Member 只看到裁剪后 Skill catalog。
- Team 恢复轮清空 Skill loader。
- Subagent 只加载定义中声明且 Project 允许的 Skill。
- Member / Subagent 不能借 Skill 访问父级未授权工具或路径。

## 16. 验收标准

第一版完成必须同时满足：

1. Chat system prompt 不再包含完整 Skill 正文。
2. AgentScope Toolkit 实际注册 `skills_or_loaders`。
3. 多个 Skill 只增加一个统一 `Skill` viewer 工具。
4. 模型能够根据 catalog 自主加载正确 Skill。
5. Skill 中写 `scripts/foo.py` 即可，不要求产品特定路径变量。
6. 模型实际 Bash 调用使用正确 Skill 绝对路径。
7. Skill 脚本通过通用 `GuardedBash` 执行。
8. Project binding、Agent 配置和权限边界无回归。
9. Skill 生命周期以真实 `Skill` 工具调用为准，并可刷新恢复。
10. 单 Agent、Team Member 和 Subagent 行为一致。
11. 后端相关回归测试通过。
12. 前端构建和 Skill 运行展示验证通过。

## 17. 风险与缓解

### 模型未把相对路径转换为绝对路径

缓解：在 `skill_instruction_template` 中给出一条明确规则和一个短示例；用 Sandbox 行为测试验证主模型与 fallback 模型。第一版不通过 Shell 字符串重写掩盖模型错误。

### Skill catalog 数量过多

缓解：只披露 Agent 配置和 Project binding 交集；description 保持简洁。后续根据实际 token 使用评估 catalog 上限或分组激活。

### Skill 名称冲突

缓解：安装时继续阻止同一 Workspace 内的归一化名称重复；运行时记录 loader 冲突 warning，不依赖 AgentScope 的后加载覆盖行为。

### 历史 Skill 不符合严格规范

缓解：第一版采用宽松加载、明确 warning，不批量重写历史 `SKILL.md`。新旧 Skill 均必须至少包含 `name` 和 `description`。

### Bash 能力扩大

缓解：Skill 不自动突破 Agent guardrails；read-only Agent 不注入 Bash；执行仍经过 AgentScope permission、`GuardedBash` 黑名单、超时和现有 Trace。

### Skill 内容在上下文压缩后丢失

缓解：第一版依赖 AgentScope Agent session 和现有上下文管理；增加长会话回归测试。若证实 Skill tool 结果会被压缩丢弃，再通过 middleware 标记或恢复最近已激活 Skill，不在第一版提前增加复杂状态。

## 18. 后续版本候选

以下能力必须基于第一版实际评测决定，不预先实现：

- 严格 Agent Skills 规范校验和 `skills-ref` 集成。
- 标准 `name` 与中文 `displayName` 双字段。
- `.agents/skills/` Project / User 自动发现。
- 受信任 Skill 安装和签名机制。
- `allowed-tools` 临时权限映射。
- PEP 723 / `uv run` 自包含 Python 脚本环境。
- Skill-aware 结构化命令执行器。
- Skill context 在压缩后的确定性恢复。
- Skill 使用效果评测、触发准确率和脚本成功率指标。
- Office 文档、Spreadsheet、PDF 等高质量内置 Skill 包。
