# AgentScope 2.0.5 人工验收脚本

> 适用分支：`codex/upgrade-agentscope-2.0.5`。执行时逐项记录 Run ID、模型、结果和异常；不要在生产 Project 中执行写入类用例。

## 1. 测试目标

本脚本验证 AgentScope `2.0.3 -> 2.0.5` 升级后的真实 Runtime 行为，重点覆盖：

- 流式文本、thinking、工具调用和 provider cache usage。
- Write / Edit 异步权限规则和 HITL 恢复。
- `finished_reason`、用户取消和最大迭代终态。
- AskUser、Plan Mode、DelegateTask Subagent 和一次性 TeamRun。
- LocalWorkspace、DockerWorkspace、Skills、上下文压缩和第二供应商。

## 2. 测试准备

### 2.1 服务与配置

1. 打开前端 `http://127.0.0.1:5173`。
2. 确认 Settings 中已配置可用的 DeepSeek API key。
3. 新建测试 Workspace：`AgentScope 2.0.5 Acceptance`。
4. 新建测试 Project，root 使用单独空目录，例如：

   ```text
   /private/tmp/yinli-agentscope-205-acceptance
   ```

5. 新建或复制一个测试 Agent，配置如下：
   - 模型：当前 DeepSeek 主模型。
   - 文件工具：Read、Write、Edit、Grep、Glob。
   - 执行工具：Bash。
   - 交互工具：AskUser。
   - Subagent：允许 `DelegateTask` 及结果 wait/read 工具。
   - 权限：先使用需要人工确认的默认模式。
6. 打开 Runs 和 Trace 页面，后续每个用例结束后都检查对应 Run。

### 2.2 记录格式

每个用例按下面格式记录：

```text
用例：M01
模型：
Run ID：
结果：通过 / 失败 / 阻塞
实际现象：
Trace 关键事件：
截图或补充：
```

## 3. P0 主链路

### M01 普通流式回答

模式：`Agent`

输入：

```text
请用三句话说明当前项目为什么要从 AgentScope 2.0.3 升级到 2.0.5。不要调用任何工具。
```

预期：

- 文本逐步流式显示，没有重复、回退或一直转圈。
- Run 最终为 `completed`，刷新页面后答案仍只出现一次。
- Trace 顺序包含 `REPLY_START -> MODEL_CALL_START -> MODEL_CALL_END -> REPLY_END`。
- `MODEL_CALL_END` 和 `REPLY_END` 的 `finished_reason` 为 `completed`。
- Runtime 中只有 `AGENT_COMPLETED`，没有 `AGENT_FAILED`。

失败判定：空答案、重复答案、Run 未结束、Reply 被记为 interrupted/error。

### M02 Thinking 与最终答案分离

模式：`Agent`，开启深度思考。

输入：

```text
先分析 AgentScope 升级时最容易遗漏的三个兼容风险，再给出一个按优先级排序的简短结论。不要调用工具。
```

预期：

- 思考区和最终答案区分离，thinking 不会吞掉最终文本。
- 如果供应商先返回 thinking-only chunk，Agent 会继续生成最终答案，不会提前完成。
- thinking 结束后，最终答案正文从首个模型 text delta 起持续增长，不会等待整轮结束后一次性出现。
- 最终答案的分片顺序与内容连续，没有重复首段、整段替换或先空白后突现全文。
- Thinking Block、Text Block 均正常闭合，Run 为 `completed`。

失败判定：只有思考没有答案、最终答案一次性整体出现、思考混入最终答案、Block 持续 running。

### M03 Write 权限、批准与规则记忆

模式：`Agent`，权限设为默认确认模式。

输入：

```text
请创建 artifacts/upgrade-smoke/permission.txt，内容为：agentscope-2.0.5 permission check。完成后读取文件并原样告诉我内容。
```

操作：

1. 出现 Write 审批时，确认路径位于测试 Project。
2. 选择批准，并勾选当前会话记住同类批准（若界面提供）。
3. 等待 Agent 继续执行 Read 并回答。

预期：

- Write 在批准前不执行，Run 进入 `waiting_permission` 或对应等待态。
- 批准后恢复原 Run，不创建第二个 Run。
- 文件真实存在且内容准确。
- Read 完成后必须输出最终答案，正文包含原样内容 `agentscope-2.0.5 permission check`。
- 审批期间同一 Run 的流式订阅保持连接；恢复后最终答案正文逐片增长，不会在完成时一次性出现。
- Trace 中权限 decision、工具状态和 Runtime approval Block 一致。
- 不出现 coroutine、规则误匹配或批准后仍无限重复询问。

失败判定：未批准即写入、批准后 Run 不恢复、工具成功但没有最终答案、最终答案一次性整体出现、路径变为 Session/Project 之外、刷新后出现两棵消息树。

### M04 Edit 权限与路径规则

继续 M03 的同一会话，输入：

```text
把 artifacts/upgrade-smoke/permission.txt 中的 permission check 精确替换为 async permission verified，然后读取并返回修改后的完整内容。
```

预期：

- Edit 使用与界面展示一致的绝对解析路径。
- 如果 M03 已记住目录规则，本次不应对同一目录错误地重复询问；未记住时应正常询问一次。
- 最终内容为 `agentscope-2.0.5 async permission verified`。
- Edit 和 Read 的 Tool Result 均为 completed。

### M05 AskUser 暂停与恢复

新建会话，输入：

```text
我要生成一份升级验收摘要，但我还没有告诉你目标读者。请先使用 AskUser 询问目标读者，不要自行猜测；收到回答后再给出五条摘要。
```

操作：在询问界面回答：

```text
目标读者是负责合并发布分支的高级工程师。
```

预期：

- AskUser 前 Run 进入等待用户状态，不生成伪最终答案。
- 回答后恢复同一个 Run，并输出面向高级工程师的五条摘要。
- 刷新等待页和完成页都不会产生重复 approval/message Block。
- Run 先 waiting、再 running、最后 completed。

### M06 运行中取消与 interrupted 终态

新建会话，输入后立即点击取消：

```text
请详细分析当前项目的 Runtime、TeamRun、Subagent、Plan、Memory 和 Event Stream 架构，每部分至少写十条，并在最后综合比较。不要调用工具。
```

预期：

- 前端很快停止继续追加文本。
- 应用 Run 最终为 `cancelled`，不能变成 completed 或 failed。
- Trace 中 AgentScope `MODEL_CALL_END` 或 `REPLY_END` 若出现 `finished_reason=interrupted`，不得显示为 completed。
- 刷新后仍保持 cancelled，不补写成功 assistant message，不触发运行后 Memory 成功提炼。

失败判定：取消后继续长时间输出、Run 被自动补成 completed、刷新后状态反转。

### M07 工具执行期间取消

输入：

```text
请使用 Bash 执行一个持续约 20 秒、每秒输出一次进度的任务；任务完成后再总结。不要缩短等待时间。
```

操作：工具开始运行并出现进度后点击取消。

预期：

- Bash/Tool Result 收口为 interrupted、cancelled 或等价非成功状态。
- Run 保持 `cancelled`，不因 AgentScope 清理事件变为 completed。
- 没有遗留持续运行的 Bash 子进程。

## 4. P0 复杂 Runtime

### M08 Plan Mode 审核、修订与执行

模式：`Agent + Plan Mode`

输入：

```text
为 artifacts/upgrade-smoke 制定一个验收计划：先检查 permission.txt，再生成 summary.md，最后读取两个文件核对。现在只制定计划，不要执行。
```

操作：

1. 等待 Plan review 界面出现。
2. 提交修订意见：`增加一步：检查 summary.md 不包含 API key 或其他敏感信息。`
3. 确认 revision 增加且内容更新。
4. 批准执行。

预期：

- Plan Mode 仍通过 ReAct middleware 运行，没有 `runtime.kind=plan`。
- 修订后 revision 递增，旧计划不覆盖新计划。
- 批准后恢复同一 Run，步骤逐项更新并生成文件。
- 最终 Plan、Agent、Run 都 completed；刷新后步骤状态一致。

### M09 DelegateTask Subagent

模式：`Agent`

输入：

```text
请委托 explorer Subagent 检查 docs/agentscope-2.0.5-upgrade-plan.md，提取“已完成改造”和“发布前外部门禁”。父 Agent 必须等待并读取 Subagent 的 result.md，再用两段话总结；不要自己直接读取目标文档。
```

预期：

- 出现 attached Subagent 任务，父 Agent 在 child 完成前不能输出最终答案。
- child 使用独立 session，状态经历 queued/running/completed。
- 父 Agent 通过 wait/read 消费 `result.md`，invocation 标记 consumed 后才完成。
- 刷新后 Subagent Block、结果和父答案仍为单一完成态。

失败判定：父 Agent 绕过委托直接完成、child 未 consumed 父 Run 就完成、结果重复投影。

### M10 一次性 TeamRun

模式：`Team`，当前 Agent 作为 Leader。

输入：

```text
组建一个临时团队审计 AgentScope 2.0.5 升级：一名成员检查权限与 Workspace，一名成员检查中断与 Runtime Event，一名成员检查测试和文档。成员完成后由 Leader 综合一份包含“结论、证据、剩余门禁”的报告。只读取项目文件，不修改源码。
```

预期：

- Leader 动态创建临时 Member 和有依赖关系的 Task，没有持久 Team/Member 选择器。
- Member、Task、Activity 和 Mailbox 在同一 DisplaySnapshot V4 Block Tree 中渐进更新。
- 所有任务成功后才进入 synthesis，综合文本流式出现。
- TeamRun 文件状态、Trace 与界面状态一致，Run 最终 completed。
- 综合阶段收到新 Mail 时会重新综合，不忽略未读消息。

### M11 Team Mailbox 干预

在 M10 至少一名 Member 仍运行时，通过 Team Console 向该成员发送：

```text
请额外确认 InjectionConfig(inject_runtime_state=False) 的设计理由，并把结论写入任务结果。
```

预期：

- Mail 投递到正确 Member，显示 sent/read 状态变化。
- Member 有未读消息时不能提交最终结果。
- 最终综合明确包含 runtime state injection 被关闭的原因。

## 5. P1 供应商、Cache 与上下文

### M12 Provider prompt cache usage

在同一会话连续发送两次相近请求：

第一次：

```text
阅读 docs/agentscope-2.0.5-upgrade-plan.md，并列出第 9 节的六个已完成改造点。
```

第二次：

```text
基于刚才读取的同一文档，把第 9 节六个改造点压缩成三条发布说明。
```

预期：

- 两轮均正常完成，usage 不重复累计。
- 如果供应商返回 cache created/hit token，Trace 出现 `MODEL_CACHE_USAGE`，数值与 `MODEL_CALL_END.metadata.provider_cache` 一致。
- 如果供应商不提供 cache usage，应显示为不可用或零，不得伪造命中。

### M13 长上下文与并行工具配对

输入：

```text
并行读取以下三个文件并分别提取五条事实：docs/architecture-overview.md、docs/memory-system-design.md、docs/project-progress-handoff.md。随后比较三者并给出不少于 1500 字的综合说明。工具调用和结果必须按文件一一对应。
```

在同一会话继续追问和要求补充，直到触发 context compression；可重复要求基于三个文件修订总结。

预期：

- 压缩前后的 ToolCall / ToolResult ID、数量和配对关系不丢失。
- Context summary 能保留任务目标、已读文件、重要发现和下一步。
- 压缩后 Agent 能继续回答，不会重复执行已完成工具或卡死。

### M14 第二供应商

把同一测试 Agent 临时切换到一个已配置的第二供应商，重复 M01、M02、M03 和 M12。

预期：

- 文本、thinking、工具、权限和 finished reason 语义与 DeepSeek 一致。
- 供应商不支持的 cache 字段按不可用处理，不影响 Run 完成。
- 切换模型后 Agent cache 已失效，不复用旧供应商实例。

## 6. P1 DockerWorkspace 与 Skills

前提：Docker daemon 正常运行。

### M15 持久 DockerWorkspace

把测试 Agent 的执行环境切换为持久 Docker，输入：

```text
在容器工作区创建 upgrade-smoke/docker.txt，写入当前 Python 版本和工作目录；然后读取文件并返回完整内容。
```

预期：

- DockerWorkspace 使用稳定 `host_workdir`，没有 deprecated `workdir` warning。
- 文件位于该 Agent 的持久宿主 sandbox，不写入用户主目录或错误 Project。
- 重启后端后再次读取，文件仍存在。

### M16 Docker Skills seed

给 Agent 绑定一个包含 `scripts/` 和 `references/` 的现有 Skill，输入：

```text
使用已绑定 Skill：先读取它的 SKILL.md 和 references，再在容器中执行它的一个只读 scripts 命令，最后报告实际解析的 Skill 根目录。
```

预期：

- Skill 被 seed 到 `/workspace/skills/<skill-name>`。
- 相对路径按 Skill 根目录解析，普通 Project 文件仍按 Project/Session workspace 解析。
- 工具和网络权限不因 Skill 自动扩大。

## 7. 权限模式补充矩阵

对同一个 Write 请求分别切换 Agent 权限模式：

| 模式 | 预期 |
| --- | --- |
| `DEFAULT` | 无匹配规则时询问；allow / deny / ask 规则按优先级生效 |
| `ACCEPT_EDITS` | 工作目录内 Write / Edit 自动允许，目录外不自动允许 |
| `EXPLORE` | Write / Edit 拒绝，Read / Grep / Glob 可执行 |
| `DONT_ASK` | 需要询问的操作直接拒绝，不产生无人处理的等待 |
| `BYPASS` | 按 BYPASS 合约执行，但显式 deny / ask 规则仍生效 |

每次切换后必须新建或使 Agent cache 失效，避免旧 permission context 干扰结果。

## 8. 验收结论

### 允许合入发布分支

必须满足：

- M01 至 M11 全部通过。
- M12 至 M14 在 DeepSeek 和至少一个第二供应商通过。
- 发布包含 Docker 能力时，M15、M16 通过。
- 没有 Run 终态反转、重复 Block、未等待 coroutine、Pydantic 错误或未知事件导致的中断。
- Trace、Runs、刷新恢复和实际文件状态互相一致。

### 阻断发布

出现以下任一问题即阻断：

- interrupted/error/exceed_max_iters 被记录为 completed。
- 取消后 Run 自动完成或触发成功 Memory 后处理。
- Write/Edit 规则返回 coroutine、误放行或越过 Project scope。
- 父 Run 在 attached Subagent 未终态或未 consumed 时完成。
- Team 存在未成功 Task 或未读 Mail 时进入最终综合。
- Context compression 破坏 ToolCall / ToolResult 配对。
- Docker 持久目录、Skill seed 或容器路径与配置不一致。
