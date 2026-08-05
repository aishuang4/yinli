# AgentScope 2.0.5 升级审计与执行方案

> 状态：兼容改造与代码级验收已完成。当前版本 `2.0.5`，实施日期 2026-07-31；真实模型与 Docker 场景保留为发布环境门禁。

## 1. 结论

本次升级应作为 Runtime 基线迁移推进，不按普通依赖更新处理。

项目已经进入 Runtime 与产品化收口阶段，单 Agent、TeamRun、Subagent、Plan Mode、HITL、取消、刷新恢复、Skills、Memory、Workspace / Project 和 Event Stream V3 / DisplaySnapshot V4 均已在线。AgentScope 位于主执行链路，且升级前存在对内部模块和 `Agent._reasoning_impl` 的运行时覆盖，因此继续停留在 `2.0.3` 会扩大应用与上游的行为差异，也会增加正式桌面发布前的迁移成本。

目标是升级到 `2.0.5`，吸收与现有 Runtime 直接相关的稳定性和权限修复，同时保持当前产品架构不变。升级必须先完成兼容改造和回归门禁，禁止只修改 `pyproject.toml` 与 `uv.lock` 后直接合入主分支。

## 2. 升级目标

本次升级需要达到以下结果：

1. 后端依赖固定为 `agentscope==2.0.5`，锁文件可重复安装。
2. 不再用 `2.0.3` 版本的实现整体覆盖 `Agent._reasoning_impl`。
3. 自定义工具符合 `2.0.5` 的异步权限接口，权限规则不会产生未等待协程或错误放行。
4. AgentScope 的中断、流式完成原因、上下文压缩和工具调用状态能够被当前 Runtime 正确消费。
5. LocalWorkspace、持久 DockerWorkspace、Skills seed 和工具执行路径保持现有行为。
6. 单 Agent、TeamRun、Subagent、Plan Mode、HITL、AskUser、Memory 和 Event Stream 不发生行为回退。
7. 升级具备明确的验证记录、发布步骤和无数据迁移的回滚路径。

## 3. 非目标与架构边界

本次升级不包含以下工作：

- 不采用 AgentScope Agent Service 替换现有 FastAPI、SQLite、Run、Trace 或 Event Stream。
- 不采用 AgentScope 内置 Agent Team 替换当前一次性 TeamRun。
- 不采用 AgentScope 内置长期记忆、RAG 或存储替换当前 Memory MVP、`memory_nodes` 或 Project 资源体系。
- 不恢复旧独立 Plan Runtime、旧独立 Subagent Runtime、旧 Memory Runtime 或持久 Team 管理。
- 不新增顶层 Chat SSE 事件，不恢复旧 RunState、DisplayEvent、旧 Snapshot 或兼容双读。
- 不在本次升级中横向扩展 Artifact、自动化、评估或 Workflow 能力。

如果升级确实要求改变 RuntimeFrame、DisplaySnapshot V4 或 Block 语义，必须先更新 `docs/chat-sse-event-contract.md`，再修改后端投影和前端消费。

## 4. 上游变化与项目收益

### 4.1 `2.0.4`

与当前项目直接相关的变化包括：

- Agent 中断时保留上下文和工具状态。
- 修复空流、被中断流和截断工具调用输入的处理。
- 在 reasoning 转 answer 时补齐 Thinking Block 结束事件。
- 修复流式 structured output 卡住问题。
- 重构 Workspace 实现，并增加新的沙箱后端。

### 4.2 `2.0.4.post1`

与当前项目直接相关的变化包括：

- 上下文压缩时保留并行工具调用与结果的配对关系。
- 修复 Workspace 默认 Glob helper 路径。
- 收紧 MCP 依赖范围为 `<2.0.0`。

### 4.3 `2.0.5`

与当前项目直接相关的变化包括：

- 统一不同 permission mode 的评估顺序和批量确认豁免传播。
- 防止会修改文件的 `find` 命令被错误识别为只读操作。
- 修复模型只返回 thinking 而没有 text 时 Agent 提前结束的问题。
- 增加 `finished_reason`、用户中断和更完整的流式结束语义。
- 支持 Agent structured output 和运行时状态注入。
- 增加 Windows PowerShell 工具和更多 Workspace 后端。

这些变化会直接影响现有权限、HITL、取消、Team Member、Subagent、上下文压缩和多供应商模型流，因此升级收益主要是核心 Runtime 稳定性，而不是新增产品功能。

## 5. 已确认的兼容风险

### 5.1 P0：主循环运行时覆盖

升级前的 `backend/src/agents/agentscope_usage_patch.py` 会把 `Agent._reasoning_impl` 整体替换为基于 `2.0.3` 的本地实现，用来给 `ModelCallEndEvent` 增加 prompt cache usage。

该覆盖会绕过 `2.0.4` 和 `2.0.5` 中新增的中断处理、`finished_reason`、thinking-only 恢复、structured output 和其他主循环修复。即使依赖版本升级成功，核心收益也会被本地旧实现覆盖。

首选改造方向是把 cache usage 采集下沉到模型响应边界或 AgentScope 官方扩展点，并通过现有 Trace / Runtime Event 附加元数据，不再覆盖完整主循环。可以在 `ChatModelBase` 包装层、`FallbackChainModel` 或模型 middleware 中完成一次最小技术验证。

只有在 `2.0.5` 确实没有可用扩展点时，才允许临时基于 `2.0.5` 重新移植 `_reasoning_impl`。临时覆盖必须同时增加：

- AgentScope 精确版本守卫，版本不匹配时拒绝启动并输出明确错误。
- 上游方法签名和关键事件字段的回归测试。
- 本地修改点说明，禁止再次无来源复制整个上游文件。
- 后续移除该覆盖的明确技术债记录。

### 5.2 P0：异步权限规则接口

`2.0.5` 把 `ToolBase.match_rule()` 和 `ToolBase.generate_suggestions()` 改为异步接口。当前 `ScopedWrite` 和 `ScopedEdit` 仍使用同步 override，并直接返回 `super()` 的结果。

在 `2.0.5` 下，该写法会返回未等待的 coroutine。权限引擎可能把 coroutine 当成真值，造成规则误匹配，并产生 `coroutine was never awaited` 警告。

升级前必须把两个 override 改为 `async def` 并 `await super()`，同时增加以下测试：

- `DEFAULT`、`ACCEPT_EDITS`、`EXPLORE`、`DONT_ASK` 和 `BYPASS` 模式。
- allow、deny、ask 和无规则四种分支。
- Project 相对路径、绝对路径、越界路径和符号链接路径。
- Write / Edit 建议规则内容与实际匹配范围一致。
- 测试运行期间不存在未等待协程警告。

### 5.3 P0：中断与结束原因

`2.0.5` 为 Model Call 和 Reply 增加更完整的 `finished_reason`，并支持 `UserInterruptEvent`。当前 Chat 通过应用层取消、后台 Run、Leader stream 和 completion 收口管理终态。

升级时必须决定 AgentScope 中断与应用层取消的职责边界：

- 用户取消仍由现有 Run API 发起，Run 终态仍以应用领域状态为事实源。
- AgentScope 的 interrupted 状态必须进入 Trace，并转换为明确的 Runtime 失败或取消原因。
- 不得把 interrupted 的 Model Call 或 Reply 默认投影为 completed。
- 取消发生在模型流、工具执行、Team Member、Team 综合或 Subagent 等待阶段时，父子状态必须一致收口。
- 不允许 projector 反向改写已经成立的 Run 终态。

### 5.4 P1：Workspace 与 Docker

`2.0.5` 重构了 Workspace 公共实现。当前工厂向 `DockerWorkspace` 传入旧参数 `workdir`；新版本仍兼容，但已标记为 deprecated，应改为 `host_workdir`。

升级验证必须覆盖：

- LocalWorkspace 的工作目录、offload context 和 tool result 路径。
- 非持久 DockerWorkspace 的创建、工具执行和结束清理。
- 持久 DockerWorkspace 的宿主目录挂载、重启复用和权限。
- Skills seed 到 `/workspace/skills`，以及 Skill 内 `scripts/` / `references/` 路径。
- Docker 中 Read、Write、Edit、Grep、Glob、Bash 和 MCP gateway。
- macOS 与 Windows 桌面后端构建中的可选依赖和运行时导入。

### 5.5 P1：模型流与上下文压缩

`2.0.5` 调整了流式 `ChatResponse` 的累计、取消和结束处理，也修复了多工具调用经过上下文压缩后的配对关系。

现有 `FallbackChainModel`、provider 参数映射、usage 统计和 FinalAnswer recovery 都需要验证。重点包括：

- 流式 chunk 只有 usage、只有 thinking、没有 text、包含多个 tool call 或被取消。
- DeepSeek、DashScope、OpenAI compatible、Anthropic、Gemini 和 Ollama formatter 的差异。
- context compression 前后 ToolCall / ToolResult 数量、ID、顺序和状态一致。
- 自定义 summary schema 在压缩失败、重试和二次压缩时保持可恢复。
- prompt cache created / hit token 不重复累计，也不因取消被标记为成功。

### 5.6 P1：事件和 Pydantic 模型

AgentScope 的 Event、Message、Block 和 AgentState 在两个版本间有字段和校验变化。项目使用 `model_dump(mode="json")` 读取原始事件，并将其归一化为应用 Runtime Event。

需要审计所有直接导入的 AgentScope 内部模块，并验证：

- 新字段不会被静默丢弃到影响终态判断。
- 未识别事件不会破坏 Leader、Member 或 Subagent 流。
- `AGENTSCOPE_TO_RUNTIME_EVENT` 只映射当前稳定需要的事件。
- DisplaySnapshot V4 仍只由 Domain Event 和 projector 更新。
- `_generate_id`、`Usage`、`ToolChunk`、Agent config 等内部导入仍存在；不能只依赖 import smoke 判断行为兼容。

## 6. 实施阶段

### 阶段 A：建立升级基线

在独立分支记录升级前基线：

```bash
cd backend
uv run pytest

cd ../frontend
npm run lint
npm run build
```

同时保存以下人工回归结果：普通回答、AskUser、权限审批、取消、Plan Mode、DelegateTask、TeamRun、刷新恢复和一次 context compression。基线必须记录使用的模型、Agent 配置、Project、运行 ID 和结果摘要。

### 阶段 B：兼容方案验证

在临时 `2.0.5` 环境或升级分支中先完成以下技术验证，并确定最终实现方式：

1. 验证不覆盖整个 `_reasoning_impl` 的 cache usage 采集方式。
2. 验证 ScopedWrite / ScopedEdit 异步 override 在 `2.0.5` PermissionEngine 中的行为。
3. 验证 DockerWorkspace `host_workdir`、持久挂载和 Skills seed。
4. 确定 `finished_reason` 和 interrupted 状态的 Runtime 映射与 Trace 方案。
5. 列出需要兼容测试保护的 AgentScope 内部导入和方法签名。

异步权限 override 等改动不要求作为独立提交继续兼容 `2.0.3`。依赖声明、锁文件和只适用于 `2.0.5` 的适配代码必须作为同一个升级序列落地，禁止把只兼容单侧版本的中间状态合入主分支。

### 阶段 C：落地依赖与兼容改造

先把 `backend/pyproject.toml` 中的依赖改为 `agentscope==2.0.5`，再更新锁文件：

```bash
cd backend
uv lock --upgrade-package agentscope
uv sync
uv run python -c "import agentscope; print(agentscope.__version__)"
```

在同一个升级序列中完成阶段 B 已确认的主循环 usage、异步权限、DockerWorkspace、结束原因和内部 API 适配，并补对应测试。依赖提交与兼容代码提交可以拆分以便审阅，但主分支上的每个最终状态都必须可安装、可启动、可测试。

依赖更新后必须人工检查 `uv.lock`：

- AgentScope 精确版本为 `2.0.5`。
- MCP 仍小于 `2.0.0`。
- 没有无关依赖的大范围升级。
- 可选 Workspace、Memory、RAG、Service 依赖没有被无意安装到桌面产物。

### 阶段 D：代码级回归

至少执行：

```bash
cd backend
uv run pytest

cd ../frontend
npm run lint
npm run build

cd ..
git diff --check
```

除现有测试外，需要新增权限异步接口、主循环 usage、中断结束原因、上下文压缩多工具配对和 Docker 参数的最小回归测试。

### 阶段 E：真实 Runtime 回归

| 领域 | 必测场景 | 验收重点 |
| --- | --- | --- |
| 单 Agent | 普通回答、thinking、工具调用、工具失败、max iters | 文本、思考、工具、终态和 Trace 一致 |
| HITL / AskUser | 等待、批准、拒绝、session allow、刷新后恢复 | 原 Run 恢复，无重复 Block 或提前完成 |
| 取消 | 模型流、工具执行、等待状态、Team 综合 | interrupted 不被记为 completed，父子状态收口 |
| Plan Mode | 生成、修订、审核、确认执行、重新规划 | middleware 仍生效，revision 和步骤投影可恢复 |
| Subagent | fork、并发、wait、read、consumed barrier | child 全终态且已消费后父 Run 才完成 |
| TeamRun | 动态 Member、Task、Mailbox、综合、用户干预 | file-first 事实源和 Block 子树持续一致 |
| Context | 长对话、压缩、并行工具调用 | ToolCall / ToolResult 配对和 ID 不丢失 |
| Skills / MCP | viewer、references、scripts、工具超时 | catalog 裁剪、权限和运行目录不变 |
| Memory | 运行前注入、memory tools、运行后提炼 | owner、Project 隔离和审计不变 |
| Workspace | Local、持久 Docker、非持久 Docker | 路径、挂载、清理和 Skill seed 正确 |
| Event Stream | 实时流、afterSeq、Snapshot fallback、刷新 | seq、checkpoint、completion barrier 无回退 |
| 模型供应商 | DeepSeek 加至少一个第二供应商 | 文本、thinking、工具和 usage 语义一致 |

浏览器回归应优先沉淀为 Playwright 用例，并复用 `project-todo.md` 中的 RuntimeFrame / DisplaySnapshot V4 P0 回归矩阵，不另建一套长期任务列表。

## 7. 验收门禁

升级只有同时满足以下条件才允许合入：

1. 后端完整测试、前端 lint、前端 build 和 `git diff --check` 全部通过。
2. 不存在未等待协程、Pydantic 校验、未知事件或 deprecated `workdir` 警告。
3. 不再整体覆盖 `Agent._reasoning_impl`；如果临时保留，必须有精确版本守卫和专项测试。
4. P0 真实 Runtime 回归全部通过，DeepSeek 与至少一个第二供应商验证成功。
5. TeamRun、Subagent、Plan Mode、HITL、取消和刷新恢复没有终态或投影回退。
6. Event Stream 边界仍为 `RUNTIME_EVENT: RuntimeFrame` 与 DisplaySnapshot V4。
7. SQLite、Team file-first、Memory Markdown 和 `memory_nodes` 不需要破坏性迁移。
8. 新旧版本对比没有明显的首 token 延迟、总耗时、token usage 或 prompt cache 统计回退；如有变化，必须记录原因和接受结论。
9. 项目进展文档记录最终版本、验证结果、已知限制和回滚状态。

## 8. 发布与回滚

### 8.1 发布

升级发布前应停止创建新 Run，并等待 active Run 结束或明确取消。发布时重启后端并清空进程内 Agent cache，禁止让同一个内存 Agent 实例跨版本继续运行。

推荐顺序：

1. 开发分支完成全部代码级验证。
2. 本地桌面开发环境执行真实 Runtime 矩阵。
3. 生成 macOS 内测包并验证冷启动、数据目录和持久 Sandbox。
4. 使用现有数据副本执行升级启动和刷新恢复验证。
5. 合入后保留一个发布周期的 `2.0.3` 回滚分支或标签。

### 8.2 回滚

本次升级原则上不修改产品 SQLite schema，也不转换 TeamRun、Memory 或 Snapshot 数据。回滚步骤为：

1. 停止新 Run，等待或取消 active Run。
2. 恢复 `agentscope==2.0.3` 对应的依赖声明和锁文件。
3. 恢复与 `2.0.3` 匹配的兼容层代码。
4. 重新同步依赖并重启后端，确保 Agent cache 重新创建。
5. 运行普通 Chat、HITL、DelegateTask、TeamRun 和刷新恢复 smoke test。

未验证前，不承诺让 `2.0.5` 中断点上的进行中 Agent reply 在 `2.0.3` 中继续恢复；回滚时 active Run 应先收口为取消或失败，历史消息、Trace、Team 文件和 Memory 数据继续保留。

## 9. 实施结果

### 9.1 已完成改造

- 依赖和锁文件已固定为 `agentscope==2.0.5`；MCP 保持 `1.27.2`，上游新增依赖仅为 `tzdata==2026.3`。
- 删除全局 `_reasoning_impl` monkeypatch，改用 `ProviderCacheUsageMiddleware` 在 `on_model_call` 采集 usage，并在 `on_reasoning` 扩展 `ModelCallEndEvent.metadata`。流式和非流式响应均保留原生事件顺序、`finished_reason`、data block、thinking-only 和结构化输出行为。
- `ScopedWrite` / `ScopedEdit` 的 `match_rule()` 与 `generate_suggestions()` 已改为异步 override，并覆盖五种 permission mode 和 allow / deny / ask 规则回归。
- `DockerWorkspace` 已改用 `host_workdir`；Agent 工厂显式设置 `InjectionConfig(inject_runtime_state=False)`，避免升级时无设计地改变系统提示词和引入 `HINT_BLOCK`。
- `REPLY_END.finished_reason` 与结构化 `error` 已进入 Runtime payload 和 Trace。`interrupted`、`error`、`exceed_max_iters` 不再映射为 `AGENT_COMPLETED`，Leader、Team Member 和 Subagent resume 均拒绝按成功收口。
- 应用 Run 取消继续是领域事实源。取消 API 已成立后，即使 AgentScope 清理阶段吞掉 `CancelledError` 并发出 interrupted Reply，也不会把 Run 改回 failed 或 completed。

### 9.2 验证记录

```text
升级前后端基线：359 passed
升级后后端全量：376 passed
升级专项：99 passed（包含在全量测试中）
前端 lint：passed（Node 24.14.0）
前端 production build：passed
源码后端启动：/api/health 200，OpenAPI 200
macOS arm64 onefile 后端：PyInstaller 6.21.0 构建成功，产物启动后 /api/health 200
git diff --check：passed
```

测试期间未出现未等待 coroutine、Pydantic 校验、未知事件或 `DockerWorkspace.workdir` 弃用警告。现存 warning 为 OpenTelemetry、Starlette/httpx 和 Uvicorn websockets 的上游弃用提示，与本次 AgentScope 升级无直接关系。

### 9.3 发布前外部门禁

当前本机未配置模型 API key，Docker daemon 未运行，因此以下项目不能在本次代码执行环境中标记为通过：

- DeepSeek 普通回答、thinking、工具调用、HITL、Plan、DelegateTask、TeamRun 和 context compression 的真实供应商回归。
- 至少一个第二模型供应商的文本、工具和 usage 对照。
- 持久 / 非持久 DockerWorkspace、Skills seed 和容器内文件工具的真实执行。

这些项目不阻塞升级分支的代码审阅，但必须在具备凭据和 Docker 的发布环境完成后才允许合入发布分支。它们只验证外部运行条件，不再要求修改本次兼容架构；若发现行为差异，回到本分支补回归测试后处理。

`docs/project-todo.md` 只保留上述尚未执行的发布门禁，不重复维护已经完成的兼容改造。

## 10. 官方参考

- [AgentScope PyPI](https://pypi.org/project/agentscope/)
- [AgentScope v2.0.4](https://github.com/agentscope-ai/agentscope/releases/tag/v2.0.4)
- [AgentScope v2.0.4.post1](https://github.com/agentscope-ai/agentscope/releases/tag/v2.0.4.post1)
- [AgentScope v2.0.5](https://github.com/agentscope-ai/agentscope/releases/tag/v2.0.5)
- [v2.0.3...v2.0.5](https://github.com/agentscope-ai/agentscope/compare/v2.0.3...v2.0.5)
