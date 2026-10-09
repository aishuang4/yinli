# 引力 YINLI 文档索引

> 状态：2026-10-09 整理。本文是 `docs/` 的唯一索引。专题文档只保留现行决定。阶段日记、被取代的快照和 PR 清单已从 `docs/` 删除，需要时看 git 历史。不设归档目录。

首次参与按这个顺序读：`AGENTS.md` → 架构总览 → 开发规范 → 进展交接 → TODO。改协议先改契约；改行为先改对应专题设计。

## 开发文档（怎么写代码、现在做到哪）

| 文档 | 用途 |
| --- | --- |
| `AGENTS.md` | 仓库根目录。当前事实、架构约束、代理协作规范。冲突时以更新、更严的专题规范为准。 |
| `docs/development-guidelines.md` | 前后端、Runtime、SQLite、测试、Git 和文档写法。 |
| `docs/architecture-overview.md` | 当前仍在线的系统架构。群聊阶段、Event Stream、定时任务控制面、默认 Job Agent 以这里为准。 |
| `docs/project-progress-handoff.md` | 当前完成度和缺口。不含日期日记。 |
| `docs/project-todo.md` | 唯一待办清单。专题文档不复制完整 TODO。 |

## 稳定契约（改协议先改这里）

前端只依赖契约里的稳定事件。

| 文档 | 范围 |
| --- | --- |
| `docs/chat-sse-event-contract.md` | Chat `RUNTIME_EVENT` / DisplaySnapshot V4。 |
| `docs/trace-run-observations.md` | Chat Trace 按 Run 的 Observation。不进 Chat SSE。 |
| `docs/group-studio-event-contract.md` | 群聊工作室独立 SSE。 |
| `docs/workflow-event-contract.md` | Dynamic Workflow 独立事件。不属于 Chat SSE。 |

## 现行设计（已落地的专题合同）

不是路线图。和总览冲突时，以更新的专题和 `AGENTS.md` 为准。

| 文档 | 管什么 |
| --- | --- |
| `docs/chat-react-runtime.md` | 单 Agent ReAct：发送、组装、Think / Act / Observe、AskUser、收口。不含 Plan、Skill、Team。 |
| `docs/team-runtime-redesign.md` | 一次性 TeamRun、动态 Member、任务、Mailbox。 |
| `docs/subagent-delegation-design.md` | Subagent 文件定义、attached fork/join、wait/read。 |
| `docs/memory-system-design.md` | 时间线、习惯和专业区。旧 Markdown 与 `memory_nodes` 不再注入。 |
| `docs/file-catalog-v1.md` | Agent / Skill / MCP / HTTP / Job 以约定目录为权威。 |
| `docs/chat-model-selection-v1.md` | Chat / Sandbox 选模型。桌面端以本机 `models.json` 为配置源。托管且配置了 key2api 时，Web 对话改用该用户的 `platform:` 模型。 |
| `docs/web-service-plan.md` | Web 托管的产品决定。账号、隔离和 key2api 的在线事实以架构总览为准。桌面登录入口尚未接到工作台。 |
| `docs/scheduled-jobs-v2-agent-activation.md` | Job Agent 能力合同、创建前门禁、到期装配 toolkit。 |
| `docs/group-studio-v2.md` | 群聊 V2：房间留下，主持流程拆掉。事件见群聊契约。 |

## 未落地设计

在线行为仍以「现行设计」为准，不要按这些草案改线上路径。

| 文档 | 还没做的部分 |
| --- | --- |
| `docs/team-member-lifecycle-v2.md` | 未上线。不要按它改现网 TeamRun。 |
| `docs/dynamic-workflows-plan.md` | 未上线。任务流仍是独立实验页，不是 Chat 模式。 |
| `docs/electron-packaging-plan.md` | 正式打包、签名、公证和双平台验证。 |

## 发布与验收

| 文档 | 用途 |
| --- | --- |
| `docs/beta-release-checklist.md` | `0.1.0-beta.*` 桌面内测发布前检查。 |
| `docs/windows-data-uninstall-test-case.md` | Windows 数据目录与卸载的可执行用例。 |

## 维护规则

- 当前事实只写入 `AGENTS.md`、`architecture-overview.md` 和 `project-progress-handoff.md`。
- 任务只写入 `project-todo.md`。
- 事件字段只在对应稳定契约中定义，其他文档只引用。
- 已完成的阶段草案在结论并入当前文档后删除，不设置归档目录。
- 旧独立 Plan Runtime（`runtime.kind=plan`）、旧独立 Subagent Runtime（`runtime.kind=subagent`）、旧 Memory Runtime 和持久 Team 管理不得恢复。当前 Plan Mode middleware 与 `DelegateTask` Subagent 委托属于 ReAct 内部在线能力，不在删除范围内。
