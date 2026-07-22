# Subagent 重设计文档审查

> 审查日期：2026-07-22
> 审查对象：`docs/subagent-delegation-design.md`、`docs/subagent-task-runtime-redesign.md`
> 审查方式：文档主张逐条对照 `backend/src/subagents/*`、`backend/src/tools/delegate_task.py`、`backend/src/server/routes/chat/leader_stream.py`、`backend/src/storage/database.py` 真实代码验证。
> 结论：**两份文档对「现状」的诊断准确，但给出的重设计方案严重超出当前系统的规模和阶段，不建议按现状实施。**

## 一、现状描述核对（准确）

redesign 文档第 3 节的缺口清单经代码验证全部属实：

| 文档主张 | 代码验证 | 结论 |
| --- | --- | --- |
| `PENDING_SUBAGENT_PARENT_CONTEXTS` 保存 live 对象，重启无法恢复 | `models.py:31` 确为进程内 dict，`leader_stream.py:369` 写入 live agent | 属实 |
| `subagent_invocations` 仅审计摘要，无 transcript/checkpoint/lease | 实际建表仅 18 个基础字段 | 属实 |
| `SubagentRunError` 会把子任务错误升级为父 Run 失败 | `manager.py:287` re-raise，经 `leader_stream.py:334 await` 上抛，该 try 仅 catch `SubagentDelegationWaiting`，异常击穿父 Run | 属实 |
| `SubagentManager` 单点承担定义选择/创建/Runner/HITL/持久化/事件 | `manager.py` 一个类六项职责 | 属实 |

诊断部分扎实，非凭空立论。**问题在于开出的药方。**

## 二、核心问题

### 1. 严重过度设计：给单用户桌面应用套了分布式任务系统

redesign 引入 lease / heartbeat / orphan 扫描 / 多 Worker claim / optimistic version 锁 / outbox 重放——这整套是为「多 Worker 争抢同一任务」的分布式后端设计的。引力是**本地单用户、单进程 Electron 应用**，不存在两个 Worker 抢同一 task 的场景。

体量对照：

- 当前整个 subagent 模块：1817 行、26 个测试。
- redesign 单文档：868 行，要求新增 5+ 张 SQLite 表、11 状态的状态机、13 个新事件类型、一整套前端组件目录。

以「待实施设计」之名，写了超过真实代码量级的重型基础设施，投入产出严重倒挂。

### 2. 整个设计建立在一个未验证的地基假设上

文档在 9.2 / 13 / 24 节反复承认：AgentScope `AgentState` 能否跨进程序列化并精确恢复父/子 ReAct **尚未验证**，需先做技术 spike，并自评「父子双 checkpoint 是本设计的最高风险」。

矛盾在于：地基（durable checkpoint）能否成立尚不可知，却已先行写出 868 行**依赖该地基**的详细设计（Scheduler、恢复流程、状态机迁移全部建立在「能恢复」之上）。正确顺序应是先 spike 得结论，再决定是否展开设计。当前顺序颠倒。

### 3. 范围爆炸，与项目阶段不匹配

项目整体成熟度约 79%，Artifact 生命周期、评估体系、桌面正式发布均未完成（见 `project-progress-handoff.md` 主要缺口）。而两份文档把 subagent 一路铺到 M5 + Phase 4：后台并发、worktree 隔离、嵌套、fork 会话、`memory` scope、`effort`、`@-mention` 等。

其中很大一部分直接照搬 Claude Code 的 frontmatter 特性矩阵（delegation-design 第 76 节表格），对「本地办公 AI 工作台」的产品定位价值不清晰。这是把「Claude Code 有什么」当成了「本项目要做什么」。

## 三、值得保留的部分（小而正确）

从两份文档中剥离出与当前系统相称、且高价值的修复，均**不需要**重型基础设施，改现有 1817 行即可落地：

1. **错误隔离**：让 `DelegateTask` 子任务失败返回结构化 error observation，父 Agent 继续推理，而非抛异常击穿父 Run。（改 `manager.py` 的 re-raise + `leader_stream.py` catch 分支）
2. **`model: inherit` 修复**：继承父本轮实际解析的模型配置，而非回退全局默认模型。
3. **异常终态收口**：Worker 顶层加 `try/finally`，保证 invocation 不会永久停在 `running`。
4. **HITL 事件关联补全**：resume 后的子事件补齐 `invocationId / subagentName / toolCallId`，projector 才能稳定合并 timeline。

设计原则中也有正确的部分，应予保留：

- 「保留外部契约、替换内部执行内核」的原地重构思路正确。
- 不恢复旧 `runtime.kind=subagent` 独立分支的约束正确。
- 「先 spike、再定契约、再实现」的顺序正确（第 25 节），只是文档自身没遵守。

## 四、处理建议

**这套设计不符合当前系统的规模和阶段，不应按现状实施。**

- **`subagent-delegation-design.md`**：保留 M1/M2 现状描述；大幅裁剪 M3–M5 的「候选能力大表」，只保留有明确产品理由的项。
- **`subagent-task-runtime-redesign.md`**：不按现状实施。降级为「将来确需后台并发时的参考备忘」。当前只落地上述 4 条隔离/健壮性修复。真要做后台任务时，也应采用「单进程 + asyncio task 注册表 + 一张轻量任务表」，而非 lease/heartbeat/orphan 那套分布式机制。
- **Phase 0 spike 提前**：若确实要探索后台/恢复方向，先用最小成本验证 AgentScope 能否跨进程序列化。这一个结论决定约 80% 的设计是否成立。

## 五、下一步

优先实现第三节的 4 条健壮性修复；后台并发、嵌套、隔离等重型能力在有明确产品需求、且 Phase 0 spike 通过前，不进入实现。
