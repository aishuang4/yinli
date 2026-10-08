# 引力 YINLI 项目进展与协作交接

> 更新时间：2026-09-15。本文只记录当前完成度、验证结果和已知缺口。

## 2026-09-15 产品功能配置档

管理员在打包前改 `config/product-features.json`，决定安装后对用户开放哪些页面和横切能力（任务流 / 群聊工作室 / Team / Plan）。前端不再写死 `OFFLINE_VIEWS`。仓库默认只关独立 Runs 页；上线建议档是 `config/product-features.packaging.example.json`（再关 Trace / Sandbox / Subagents / Workflow）。`chat` / `settings` 强制开启。关闭页面只 404 管理 API，Chat `/api/chat/runs` 仍给取消、重跑、订阅和 Team 干预用。

## 2026-09-14 上线前核查

- 分支 `feat/file-catalog-agents`：后端全量 `769 passed, 2 skipped`（含本日 ReAct 上下文改造）；前端 `lint` / `build` / `vitest` 通过。`master` 上因 `e9cc7b9`（Agent 跨 Project 可用）遗留的 3 个过时测试已在本分支修复。
- Chat 消息渲染新增 `EchartsContent`：只按内置 Skill `yinli-charts` 合同识别闭合的 ```echarts / ```echarts-json 围栏，普通 ```json 不渲染，只允许 `bar` / `line` / `pie` / `scatter`，拒绝 `dataset` / `graphic` / `calendar` / `custom`。解析逻辑在 `echartsMarkdown.ts`，echarts 运行时通过 `React.lazy` 独立分包（约 560 KB，仅在出现图表时下载），首屏 chunk 不受影响。
- 前端引入 `vitest` 作为单测框架（`npm test`），首个用例覆盖图表围栏解析；RuntimeFrame / reducer 单测仍在 TODO。
- `word-documents` 系统 Skill 完整包（4 个脚本、`references/`）在 `artifacts/skills/word-documents/`，启动时 seed 到运行时 `skills/` 目录并覆盖旧副本；开发机上出现只剩 `SKILL.md` 的目录属于 seed 前的本地残留，不是仓库缺失。脚本只依赖标准库。
- 2026-09-14 桌面打包版人工验证：配置多个真实模型供应商并在 Chat 中切换可用；模型输出的是 ```echarts 围栏并在前端渲染成功，与严格围栏版 `EchartsContent` 规则一致，无需放宽到 ```json。Docker 沙箱门禁仍未验证。
- 仓库尚无 git tag；Debian HTTP 部署方案无认证无 TLS，不能作为对外上线形态。

## 2026-09-14 ReAct 上下文管理改造

按桌面端运行轨迹核查后确认的问题与处理（设计见 `docs/architecture-overview.md` "ReAct 上下文管理"）：

- Skill 注入越界：`_merge_file_catalog_skills` 曾无条件把项目 Skill 和全部系统 Skill 注入任意 Agent；现在只有 `yinli-catalog` 无条件注入，其余按 Agent 配置裁剪（`ALWAYS_INJECTED_SKILL_IDS`，测试 `test_chat_skill_injection_scope.py`）。
- 启动崩溃：历史自进化 Skill 的 `trigger` 超过 200 字导致 `_load_user_skills` 抛 `ValidationError`，桌面端升级后无法启动；已改为字段截断 + warning、单条失败跳过，并用含超长字段的桌面 DB 副本验证启动成功。
- 每轮新建 Agent 实例：system prompt 正文进了缓存键，记忆摘要每轮变化导致每轮新实例、AgentScope 会话记忆丢失且旧实例泄漏。现已去掉 prompt hash，加 LRU（48）/ 闲置 6h 淘汰，配置变化时把上下文搬到新实例（`agents/session_context.py`）。
- 双份历史：`chat_messages` 曾扁平拼进 system prompt，与 AgentScope `state.context` 重复；现在 system prompt 只放稳定内容，历史只存在于 `state.context`，进程重启后用最近 20 条正文重建。本轮附件说明改为用户消息的 `<attached_files>` 段。
- 时间戳：精确到秒的 `当前时间` 写在 system prompt 中，每轮破坏供应商 prompt cache；改用 AgentScope `InjectionConfig`（上海时区、`%Y-%m-%d %H:%M`、0.5h 间隔）以 HintBlock 注入。
- 记忆注入：节点树重复标题、无总预算、`# Summary` 与来源章节重抄；现在总预算 2500 字、按标题去重、每日 3 条、Summary 优先。
- 无意义模型调用：readiness 在规则已判定 ready 时仍调模型且结果不可能改判；`INTENT_CATALOG` 只有 `react` 时 router 仍调模型。现在规则优先，仅模糊输入 / 多 architecture 时调模型，两者均清空系统 Agent 上下文并把 `modelCalled` / `durationMs` 写入 trace。
- 最终回答按模型生成区分：每次生成的正文从第一个 token 起流式投影；这一次生成没有工具调用且回复完成时，消息 `data.answerGeneration` 指向该正文块。旧快照没有该字段时，已完成消息里的 `kind=text` 仍当作最终答案（契约见 `docs/chat-sse-event-contract.md`）。
- 中途补充消息：`RunSteerMiddleware` 原来写 `agent.memory.add`，AgentScope 2.0.5 的 Agent 没有 `memory`，实际是 no-op；现在写入 `state.context`，且只在下一次 reasoning 前写，避免插在 tool_calls 与 tool 结果之间。
- Skill 正文生命周期：`Skill` 工具读到的 SKILL.md 只是一条 tool result，截断 / 压缩 / 实例重建后丢失，模型下一轮常常再读一遍。新增 `SkillContextMiddleware` 捕获正文、检测缺失并以 `<activated-skill>` HintBlock 补回，重启后由 `skill_runs` 记录恢复；`SkillRun` 每轮关闭的语义不变。system prompt 新增稳定的 `## Skill Context` 规则。真实 Agent 烟测：读取 → 压缩清空 → 下一轮自动补回通过。
- 桌面端真实轨迹复核（qwen3.8-27b，5 轮 24 次模型调用）：同一轮内 `cache_input_tokens` 从 5760 逐步升到 25728，前缀 cache 正常命中；但每个新轮次的第一次调用 `cache_input_tokens` 都是 0。原因是每轮结束后台新增一条历史记忆节点，下一轮的记忆摘要（在 system prompt 里）变了几十个字符。现在记忆注入文本按 `(agent_id, session_id)` 冻结（`routes/chat/memory_prompt_cache.py`，TTL 6h，Memory 页面手动改写时失效），会话内 system prompt 字节一致。
- Trace 观测：`MODEL_CALL_START` 增加 `contextStats`；`TOOL_CALL_END` / `TOOL_RESULT_START` 增加脱敏 `inputPreview`；`sanitizer` 新增 JSON 凭据模式。
- 验证：后端全量 `769 passed, 2 skipped`；真实 AgentScope 实例烟测（重建历史 → HintBlock 注入 → 配置变化搬运上下文）通过。真实供应商多轮 token 曲线尚未复测，记在 TODO。

## 2026-09-15 上下文窗口与压缩治理

在 Chat 顶栏上下文占用 meter（`e7439df`）之上，按 `docs/architecture-overview.md` "上下文窗口与压缩治理" 落地：

- 窗口按模型：`ModelEntry.contextSize`（models.json / 模型管理页新增字段，0 = 按供应商默认）+ adapter 默认表 + 降级链取最小；此前所有模型都用 AgentScope 默认 32768，128k 模型在 26k 就触发压缩。
- 估算同源：`estimatedTokens` 改为与 AgentScope `count_tokens` 同一算法（utf-8 字节 / 4 + 工具 schema JSON + DataBlock 2000），`MODEL_CALL_END` 后记录 `estimateErrorPct` 供校准；此前是字符折半，另一套口径。桌面轨迹粗算（52.9k 字符 + 工具 schema → 27.7k 实际 tokens）显示 bytes/4 对中英混合内容偏差不大，不另加 CJK 权重。
- `ContextConfig`：`trigger_ratio` 0.8 → 0.75、`reserve_ratio` 0.1 → 0.2；summary schema 去掉从未被模板渲染的 `title` / `summary` 字段，改用 `additionalProperties=True` 容忍；`maxLength` 保持 2000 / 1200（jsonschema 严格校验，收紧会让压缩失败中断运行）。
- 一级无模型整理 `ContextHygieneMiddleware`：早于最近 2 轮、超过 600 字的工具输出存根化，旧图片换文字占位；`Skill` 正文不动。累计计数进入 `CONTEXT_USAGE`。
- 压缩可观测 `ContextCompressionMiddleware`：真正压缩时记录前后消息数 / 摘要长度 / 耗时，写 `CONTEXT_COMPRESSED` trace；`state.session_id` 设为 Chat session，offloader 目录不再随机。
- 会话状态落库：新增 `session_context_state` 表保存 `state.summary` + 可 JSON 化的 `middle_context`（已启用 Skill 正文、治理计数），每轮结束写、实例重建时恢复、会话删除时清理。
- system prompt 瘦身：Clarification / Final Answer 契约从约 2.5k 字压到约 1.1k，问题数量 / 类型细节留在 `AskUser` 工具 description，最终回答改为正文契约；历史记忆索引只留 `date | nodeId | title`，节点 `summary` 改为"请求要点 → 结果要点"一句话（≤170 字，去 Markdown 噪音），不再复制 700 字原文。
- 观测：`contextStats` / `CONTEXT_USAGE` 新增 `promptSections`（system prompt 分段字数）、`toolSchemaChars`、`dataBlocks`、`compressionCount`、`stubbedToolResults` / `droppedImages` / `hygieneSavedChars`；顶栏 tooltip 展示压缩次数、整理统计、估算偏差和最大的几段 prompt。`contextUsage.ts` 不再依赖 `runtime-stream/selectors`（避免把 Block 渲染模块拉进纯数据层，vitest 因此能跑）。
- 验证：后端全量 `790 passed, 2 skipped`；前端 `vitest` 12 passed、`tsc -b`、`eslint` 通过。
- 2026-09-15 桌面打包版真实供应商人工验证通过（`contextSize=6144` 的测试配置，6 轮用例）：顶栏窗口 / 阈值 / 分段字数正确；无工具直接回答为纯文本，用过工具后的收尾也是纯文本正文；多轮读文件后触发 `CONTEXT_COMPRESSED` 且回答不泄出摘要；旧工具输出被存根化后模型能重新读取回答；重启后同会话不重读 Skill、压缩状态保留。`estimateErrorPct` 的长期分布仍待观察。

## 2026-09-15 记忆沉淀与注入修正

按 `docs/memory-system-design.md` "2026-09-15 沉淀与注入修正" 落地：

- 注入 query-aware：静态 12 条标题索引移出 system prompt，改为按本轮用户消息召回 ≤ 4 条带摘要的节点附在用户消息后；system prompt 记忆段只剩 Markdown 摘要且继续按会话冻结。`_rank_terms` 中文 2 字滑窗，召回不计 use_count，写 `MEMORY_AUDIT.memory_recall`。
- 提炼合成一路 `MemoryReflectionAgent`：无工具、一次看三份文件 + 用户消息 + 最终回答 + 脱敏工具轨迹（`build_tool_trajectory`），输出带 scope 的操作；模型说不写就不兜底。一轮基础设施模型调用上限 5 → 3。
- user / global 层待确认：新表 `memory_markdown_candidates` + `GET/PATCH /api/memory/markdown-candidates`，Memory 页新增"待确认记忆" tab（入口显示待处理数）与设置页两个开关（默认开）。agent 层直接写。
- 准入一轮一评：`evaluate_turn_admission()` 结果同时给节点持久化和 Markdown 提炼。
- 历史节点会话日聚合 + 90 天未命中自动 archived（每进程每天一次）。
- 验证：后端全量 `800 passed, 2 skipped`；前端 vitest 12 passed、`tsc -b`、eslint 通过。真实供应商下的召回相关性、候选队列体感尚未验证。

## 2026-09-15 Team / 群聊对齐单 Agent 治理

新增 `routes/chat/runtime_governance.py` 收拢共用组件，三条 Runtime 改为同一入口：

- middleware：`build_context_governance_middlewares(skill_context=)` = SkillContext（可选）+ ContextHygiene + ContextCompression。单 Agent 另加 RunSteer；Team Leader / 综合不挂 Skill，Member 按是否带 Skill 决定；群聊 Host 不挂 Skill，Worker 挂。
- 记忆：`build_memory_prompt_and_recall()` 返回（冻结的 Markdown 摘要，按文本召回）。Team Leader / 综合复用 `ChatRunContext.memory_prompt` / `memory_recall_text`；Member 按任务标题 + 简述召回并写 `MEMORY_AUDIT`（scope_label `team_member`）；群聊 Worker 带自己 Agent 的 Markdown 摘要（按 studio+member 会话冻结），按任务卡目标 + 开放工作项召回，认领轮不召回。定时任务路径也改为同一函数（不冻结）。Team 此前根本没有记忆注入，AGENTS.md 的描述已修正。
- 会话状态：群聊 Host / Worker 跨轮，`prepare_agent_governance` + `restore_agent_session_state`（实例重建时）+ `persist_agent_session_state`（每轮 finally）；Team 会话是 run-scoped，只做 `prepare`。
- 观测：Team `_run_agent_stream` 新增 `on_model_call` 回调 → `observe_model_call_event()` 写 `CONTEXT_COMPRESSED` / `CONTEXT_USAGE` trace（scope `team_leader` / `team_member` / `team_synthesis`）。群聊暂不写（run_events 尚无 `source` 字段）。
- 验证：后端全量 `807 passed, 2 skipped`。

## 2026-09-15 Team / 群聊链路修正

按 `docs/project-todo.md` 审查结论落地（后端全量 `819 passed, 2 skipped`）：

Team（`team_runtime/`）：
- 失败任务自动重派一次（`store.retry_failed_task`，新 session、`lastReviewReason` 带失败原因进用户提示、runtime key 含 retryCount），仍失败保留 failed 并把依赖它的任务级联失败（`retryable=False`）；`TeamCoordinator.run()` 只要有成功任务就返回，综合阶段做部分综合（prompt 明说哪些任务未完成），`complete_run` 允许 `partial`；全部失败才抛错。`service.py` 的综合重试循环改为只在还有未结束任务时回到 coordinator，避免部分失败时死循环。
- 上限：成员 6、任务 12（工具层拒绝并提示 Leader），单任务 10 分钟 `asyncio.wait_for` 超时，`team_submit_result` ≤ 6000 字；`create_task` 做依赖环检测。
- 成员 Write/Edit 限制在 `<session workspace>/team/<run>/<member>/` 私有目录并写进 system prompt；综合输入改 JSON，结果邮件不重复、邮件正文裁 600 字、最多 40 封。
- 2026-09-15 桌面 Team 复测：成员一写文件就整队“卡住”。根因是 Write/Edit 的 `base_dir` 仍是 session cwd、`scope_dir` 是更窄的成员目录，权限检查抛 `Tool base directory is outside filesystem scope` 并打断 session；同时 `if blocked: raise` 会在仍有可执行任务时误杀整队，`task.failed` 关闭 Block 后重派过程不再投影。已改为 Write/Edit base 收窄到成员目录、越界改 DENY、失败成员回 idle、blocked 与 claimable 分开判断、重派重新打开 Task Block，并在 Console 展示 `failureReason`。
- 2026-09-15 Leader 协调复测：4 人流水线在 Leader 阶段整轮失败（`Team session 达到最大 ReAct 轮数`）。根因是 Leader 一轮只调一个工具、逐个 spawn / create_task、从不调用 `team_finish_coordination`，16 轮触顶。已把 Leader 预算提到 64，并在已有成员+任务时自动收口进入 executing。
- 开启人工审批的 Agent 进入 Team 模式时，Leader 先给用户发一封 `notice` 邮件说明需审批工具会被自动拒绝。

群聊（`group_studio/`）：
- 归档记忆收口 `archive_memory.py`：Goal + 黑板 decisions + 已解决问题 + 完成工作项 + DoD 拼成纪要，走 `evaluate_turn_admission` → 历史节点 → `MemoryReflectionAgent`（归档结论放行 Markdown 提炼，user / global 仍进待确认队列），归属第一个 Worker 的 Agent，写 `MEMORY_AUDIT.group_archive_*`。
- 验收：新 Host 工具 `group_review_work_item(accept|rework)`，`group_work_items` 新增 `review_status / review_note / rework_count`；rework 退回 open 保留认领人并点名其继续，Worker 提示里带验收意见；Host 验收期提示列出待验收 / 退回中项，全部 accepted 才 propose_close。前端工作项列表显示 ✔ / ↺。
- 2026-09-15 桌面群聊复测：`group_propose_close` 会 `cancel_active_run(pause)` 掐掉正在跑的 Host 轮，空气泡 + 像报错；本地草稿点成员会打 `gstu_local_*/available-agents` 404。已改为提议关群不清自己的 Run，本地草稿不再请求该接口。
- 2026-09-15 结项后历史消失：列表故意丢掉 `archived`；from-chat 的 Chat session 没初始化 `messages`，回 Chat 点会话 404。现改为归档保留在群聊列表（删除才隐藏），并补上空消息列表。前端不再把 `illegal_transition` / `not_found` 原文弹给用户。
- Worker 崩溃只移出本人，`_continue_after_worker` 续跑队列或回 Host；黑板 decisions > 20 自动把最早 8 条归并为一条；Worker shell 黑名单加破坏性命令（bypass 免疫）。

未做（记 TODO）：Leader 验收步骤、Team 跨轮连续性、成员 Bash 越界写。群聊 V2 第一版已落地。

## 本轮模型配置改造

- 已实施 [Chat 模型选择与 models.json 配置 V1](./chat-model-selection-v1.md)：Agent 不再选模型，Chat / Sandbox 按配置 ID 选择，所有连接与 Key 从用户数据目录 `models.json` 读取。
- Run 固定完整连接快照，Team / Subagent / 审批恢复 / 后处理继承；会话、Job 和工作室存配置 ID。旧 SQLite 与安全凭据库仅在首次迁移读取。
- 后端全量 `690 passed, 2 skipped`，最终模型专项 `14 passed, 1 skipped`；前端 lint/build、Electron 编译、新模块 Ruff 检查通过。旧基线测试记录见下文，不代表当前结果。
- 双模拟供应商实际 HTTP 请求、浏览器配置保存与会话选择、macOS arm64 后端打包及启动检查通过。Windows CI 已添加但未执行，完整安装包安装与真实供应商矩阵仍待发布验收。

## 当前阶段

项目已从功能原型进入 Runtime 与产品化收口阶段。当前定位是本地优先的桌面 Agent 工作站：ReAct 是唯一在线 architecture，`agent` / `team` 是显式 executor mode。

当前综合成熟度约 79%：Runtime 内核、主要管理页面和 Workspace / Project Phase 0 至 Phase 3 可用，Artifact、自动化、评估和正式桌面发布仍未完成。

### AgentScope 2.0.5 基线

- Runtime 依赖已从 `2.0.3` 升级到 `2.0.5`，锁文件保持 MCP `1.27.2`，仅新增上游必需的 `tzdata`。
- 已删除覆盖 `Agent._reasoning_impl` 的 usage patch，改由原生 middleware hook 采集 provider prompt cache usage。
- Write / Edit 异步权限接口、Docker `host_workdir`、Reply `finished_reason` / `error` 和取消终态均已完成兼容适配。
- 2.0.5 runtime state injection 最初在工厂中关闭；2026-09-14 起改为显式启用（上海时区、半小时刷新、不外发 hint 事件），用来替代 system prompt 里的时钟。
- 后端全量最近一次：`671 passed, 4 failed, 1 skipped`（2026-08-31，`master`）。失败项与群聊无关：Plan Mode 集成、默认 workspace 目录、定时任务 catch-up、过期桌面 Project 路径。
- 前端 lint/build、源码后端启动、OpenAPI、macOS arm64 onefile 构建和产物健康检查此前已通过。
- 真实供应商矩阵与 Docker 执行仍是发布前门禁，任务只记在 `docs/project-todo.md`。不阻塞代码审阅；合入发布分支前必须在具备凭据和 Docker 的环境完成。

### Workspace / Project

- 已建立 `Workspace -> Project -> Session -> Run` 归属模型、默认记录和历史数据回填。
- Chat 在创建 Agent 前解析不可变 `RuntimeContext`；Session 不能跨 Project 继续。
- Project root 与 Agent filesystem scope 取交集，不相交时拒绝运行。
- TeamRun、Subagent、Memory、Trace、Domain Event 和 Projection stream 已继承 Workspace / Project，并按当前 Project 隔离。
- Project resource binding 裁剪 Skill、MCP 和 HTTP Tool（与 Agent 配置取交集）。普通 Chat Agent 按 Workspace 可用，不再要求 Project Agent binding。
- 已有 Project 在升级时建立一次性资源基线，新建 Project 快照绑定创建时的 Workspace 资源；后续新增资源不会自动授权。
- Chat 历史区按 Workspace / Project 分类，点击 Session 自动恢复其归属；新对话在首次输入前选择或创建工作上下文。管理侧栏保留资源上下文切换入口。
- Session、Runs、Trace、Memory 与 Agent、Skill、MCP、HTTP Tool 管理已读取当前上下文；新建持久资源会显式绑定当前 Project。
- `Workspace + Project` 使用 bootstrap 组合 API，Project root 校验失败不会残留空 Workspace。
- 正式 Artifact ownership 与浏览器级跨 Project 自动化回归仍待实现。

## 已在线能力

### 群聊工作室

- 独立于 Chat Team Runtime：Chat 顶栏 / 快捷键进入独立 pane，SQLite `group_*` + `/api/group-studio` + 独立 SSE。
- V2 已落地：进群 / 新建先空白画布，第一条消息才落库 `open`；无确认开始、无可见 Host、无阶段机。人类 `mentions[]` 点名，空则全员一轮，说完停。
- Worker 只留 `group_post_message`，`PermissionMode.BYPASS`；通知点击打开工作室。
- 消息区与 Chat 共用滚到底部按钮。SSE `afterSeq` 缺省不重放。结束 `done` 后走归档记忆。

### Chat 与运行状态

- Readiness Gate、Intent Router、ReAct Runtime 和 ToolScope。
- 单 Agent 流式执行、AskUser、HITL、取消、重跑和刷新恢复。
- 后台 Run、Event Stream V3、DisplaySnapshot V4 和 Trace。独立 Runs 页由产品档控制，Chat 仍使用 `/api/chat/runs`。
- send / resume 共用 Leader 流处理和 completion 收口；顶层 Run 区分用户输入、权限审批和外部执行等待，终态不可逆。
- Plan Mode 通过 middleware 接入且仅支持单 Agent；已具备持久化 revision、人工审核、自然语言模型修订、确认后执行、重新规划和实时步骤投影。删除的是旧独立 Plan Runtime（`runtime.kind=plan`），不是当前 Plan Mode 能力。

### Event Stream

- Event Stream V3 已原子切换生产 Chat，只保留 `RUNTIME_EVENT: RuntimeFrame` 与 `DisplaySnapshot V4` 两个前端边界。
- 后端在线链路为 `SourceEvent -> ChatProjectionNormalizer -> DomainEventRepository -> ProjectionRunner -> per-kind projector -> BlockLifecycleCoordinator -> ProjectionStreamStore -> SSE`；同一 Run 使用全局 `stream_ordinal`、lease/fencing、周期 Snapshot + tail replay、retention、afterSeq fallback 和 completion barrier。
- `message/text/reasoning/thinking/tool/skill/approval/plan/artifact/error/subagent/team/team-member/team-task/team-activity/team-mail` 已注册独立 projector。Subagent 和 Team 的完整领域状态仍保留在各自存储，Chat 只消费嵌套 Block 摘要。
- 前端 Chat 与 Sandbox 已切换到严格 RuntimeFrame decoder、streamAdapter、Block Tree reducer、kind registry 和 Generic Block fallback。HITL renderer 只提交命令，状态由后端 Frame 回流。
- `event_hub`、RunState schema、DisplayEvent projector/reducer、旧 stream adapter、固定 Process/Plan 组件和工具结果反解析已删除；旧测试 fixture 已物理删除，不存在双读、双写或 compatibility fallback。
- SQLite 检测到旧 Chat 投影表或非 V4 Snapshot 时执行一次性运行域 reset，不转换旧数据；后续数据库只写 V4 Snapshot。
- Event Stream 专项测试现有 13 项，覆盖 aggregate transaction、跨 aggregate/并发 ordinal、非法生命周期、父子级联 close、零 Frame checkpoint、runner fencing、Snapshot + tail replay、retention fallback、clarification/HITL 和 terminal done。
- HITL 的 `RUN_WAITING` 只作为等待预告，实时订阅会持续到可操作的 approval Frame；resume 终态统一写回最终 V4 Snapshot、关闭订阅，并移除同一 Run 的旧 waiting 占位消息，避免刷新后重复显示进行中与完成两棵 Block Tree。

### TeamRun

- 每次 Team 请求创建独立 TeamRun，Leader 动态创建临时 Member 和任务。
- Member session 隔离，通过 Task List、Mailbox 和任务结果协作。
- 用户可向指定成员或全员发送 Mailbox 消息。
- Team 以 file-first 状态和 `events.jsonl` outbox 为事实源，不再给每条事件附加完整 snapshot；Chat 将 Run、Member、Task、Activity、Tool 和 Mail 分别投影为规范化 Block 子树。
- Team Member Lifecycle V2 未作为 master 行为上线。现网已有 Mailbox `dispatchMode`；Reviewer、原 Task rework 与跨进程恢复仍未做。在线基线仍是 `docs/team-runtime-redesign.md`，目标草案见 `docs/team-member-lifecycle-v2.md`。
- 前端 Team Console 只从 DisplaySnapshot V4 派生任务、成员、过程和 Mailbox，并支持通过 active TeamRun Mailbox API 向成员或全体发送干预消息；命令结果仍以 RuntimeFrame 回流为准。
- 持久 Team CRUD、template、固定成员列表、`/api/teams`、`teams` / `team_members` 表已经删除。

### Subagent

- Subagent 是 ReAct 内部 `DelegateTask` 委托；删除的是旧独立 Subagent Runtime（`runtime.kind=subagent`），不是当前委托能力。
- Markdown + YAML frontmatter 文件定义和项目级 / 用户级扫描。
- ReAct 内 `DelegateTask` attached fork、并行 fresh-context 子 Agent、`SubagentTaskWait` 和 `SubagentResultRead`。
- 工具、Skills、Memory 和 permission mode 按父权限上限裁剪。
- Session 内每个 invocation 使用 `task.json`、`status.json`、`result.md` 和 `transcript.jsonl` 作为文件事实源；SQLite 保存控制台查询索引。
- 父 Run Completion Barrier 强制所有 attached child 终态且 consumed；Chat 和 Subagents 控制台分别展示任务状态与结果消费状态。
- detached、嵌套、跨进程精确恢复、运行中消息和 worktree isolation 尚未开放。

### Skills

- Chat Runtime 已从关键词预选和完整 `SKILL.md` 注入迁移到 AgentScope 原生 `skills_or_loaders` 与内置 `Skill` viewer。
- Workspace / Project binding、Agent 配置和请求显式 `skillIds` 继续作为 catalog 白名单；Agent cache key 包含包路径和 `SKILL.md` 修改时间。
- 单 Agent、Team 临时 Member 和 `DelegateTask` Subagent 已统一传递 Skill 包路径；Team 协调、综合和恢复轮继续禁用 Skills。
- Skill 生命周期以真实 viewer 调用为准，viewer 不重复显示普通工具卡片；本地 Bash 与网络权限已解耦。
- Skill zip 安装已增加标准 frontmatter、符号链接和兼容 warning 诊断，详情页展示非阻断提示。
- 持久 Docker sandbox 会 seed Skill 到 `/workspace/skills`；非持久 Docker + Skills 当前明确拒绝启动，避免宿主路径在容器中静默失效。
- 尚需用至少两个模型供应商做 Sandbox 行为验证，重点检查 Skill 相对路径转绝对路径和 Office script 调用稳定性。

2026-07-20 真实模型 smoke test：

- DeepSeek `deepseek-v4-flash` 已完成 `Skill -> Read references/ -> Bash scripts/` 全链路，最终结果和 SkillRun 均正确收口。
- 测试 Agent 关闭 network 后仍可运行本地 Bash，验证 execute 与 network 权限已解耦。
- macOS 当前运行环境提供 `python3`，不提供 `python`；Skill 示例和后续 Office Skills 应显式使用 `python3`，或在脚本前探测解释器。
- DashScope `qwen-plus` 已到达真实供应商接口，但返回 `401 invalid_api_key`，因此第二供应商行为验证仍未完成。
- 真实 Run 发现 Runtime Event 路径仍把 `Skill` viewer 写入 DisplaySnapshot 普通工具项；已在 projector 层过滤并增加回归测试。
- 真实 Run 中一次 Bash 失败后成功恢复，旧 Trace 摘要曾被历史 failed 事件锁死。2026-09-18 起 Trace 身份改为 Chat Run，状态直接读 `chat_runs`，时间线来自 `trace_observations`，不再用 `run_events` 启发式。generation 第二层改为快照 `state.context`，并在 thinking / 工具声明齐了再关，避免 `MODEL_CALL_END` 时尚无正文。

### Memory

- user / agent / global Markdown 记忆。
- SQLite `memory_nodes`、`memory_node_tree` / `memory_node_query`。
- MemoryAdmissionGate、运行前注入、运行后提炼和审计。
- disabled / zvec provider、召回反馈、Memory Wiki 和 Skill 自进化 MVP。
- 当前方向是稳定和迁移验证，不继续横向扩张实验能力。

### 网络工具

- `HttpRequest` 用于显式 API 请求；`WebFetch` 用于读取已知 HTTP(S) 网页（公网 / 本机 / 内网）并提取正文，两者使用独立 ToolScope。
- `WebFetch` 已接入主 Agent、Team Member、Subagent 和 Plan Mode；允许公网 / 本机 / RFC1918·ULA 内网，仍拦截链路本地、云元数据、本机 API 端口、凭据 URL、危险重定向、超大响应、非文本内容和外部 prompt injection。
- WebSearch 尚未实现，不能把 WebFetch 描述成网络搜索能力。

### 定时任务与通知

- 系统内置 `system-websearch-job`（名称 `WebSearch`）已作为默认 Job Agent 接入：新旧数据库启动时幂等补齐，主模型为 `deepseek-v4-flash`，必注入 `WebFetch` / `HttpRequest`，跨 Workspace 可用且不可删除或改为 Chat Agent。系统身份只豁免 Agent 自身 binding，外部资源继续按 Project 白名单裁剪。
- 定时任务 firing 终态写入通知分发器，应用内通道落 `user_notifications`。
- Chat 工作台顶栏设置按钮左侧有任务通知铃铛；独立 SSE 推送未读数，断开后轮询。
- 设置页可配置通道。应用内始终开启；邮箱 / 微信 / 飞书仅占位，不能启用。
- 通知不进入 Chat SSE，也不新增 `JOB_EVENT`。

### 管理与桌面端

- Agent CRUD、智能创建、导入导出、模型、工具、Skills、MCP 和权限配置。
- 系统 `default` Agent 已持久化并在管理页单独展示，可配置模型、系统提示词、Skills、MCP、HTTP 工具和 Guardrails；该 Agent 跨 Workspace 可见且禁止删除。
- Agents、Subagents、Tools、MCP、Skills、Memory、Trace、Sandbox、Workflow、Settings 页面。入口由 `config/product-features.json` 控制，默认只关独立 Runs 页。
- Electron 开发窗口、后端启动和 macOS / Windows 内测打包脚本。

## 当前验证

最近一次完整验证：

```text
backend pytest: 731 passed, 2 skipped (2026-09-14, feat/file-catalog-agents)
frontend lint / build / vitest: passed (2026-09-14)
  build 仅剩 EchartsChart 懒加载 chunk 超过 500 KB 提示，属按需下载
/api/teams: 404
legacy Agent Builder mode=team: 422
legacy teams / team_members tables: removed
group studio PR 测试（pr1–pr14）：此前在 feat/group-studio 上 116 passed
```

2026-07-23 真实浏览器回归：

- 普通回答完成后 `streamDone=true`，afterSeq replay、Snapshot fallback 和刷新恢复正常。
- AskUser 在 `RUN_WAITING` 后正常显示可操作 approval；提交“开发”后 tool、approval、text 和 message 全部收口，刷新恢复后只有一棵完成态 Block Tree，无持续转圈或旧 waiting 占位。
- `DelegateTask(explorer)` 完成前父 Agent 保持等待，父 Agent 消费子结果后才输出最终回答；刷新后 `kind=subagent` 仍为单一完成态 Block。
- Subagents 页面可查看 completed invocation、结果、耗时和完整 transcript。

2026-07-27 Team 流式回归（AgentScope 2.0.3 基线）：

- Team、Member 和 Task 按运行进度逐步进入同一 DisplaySnapshot Block Tree，任务完成数从 `1/4` 连续更新到 `4/4`。
- 综合阶段逐条转发 `TEXT_BLOCK_DELTA`，最终答案从首个文本增量持续增长；`FINAL_ANSWER_REPLACE` 只做终态一致性校正。
- 后端完整测试 359 项、前端 lint/build 和浏览器真实运行均通过，最终 Chat Run 正确收口为 `completed`。

2026-07-31 AgentScope 2.0.5 升级验证：

- 新增 17 项升级专项回归并纳入全量测试，总计 `376 passed`。
- 前端 lint 和 production build 通过，最大主 chunk 仍约 410 KB。
- 源码后端和 macOS arm64 onefile 后端均成功启动，`/api/health` 返回 200；源码 OpenAPI 路由加载完整。
- onefile 产物由 PyInstaller 6.21.0 构建，AgentScope 内置工具脚本已被收集；Windows 真机仍按桌面发布计划单独验证。

前端已通过轻量 Design 适配入口移除 `@agentscope-ai/design` 完整 barrel 的隐式依赖，React runtime 单独分包；当前最大 chunk 约 410 KB，生产构建不再产生大 chunk 警告。

## 主要缺口

1. Team、Subagent、Plan、HITL、群聊工作室和刷新恢复缺少完整 Playwright / 浏览器回归。
2. Artifact 还没有正式生命周期，大体积工具结果主要依赖 Trace 摘要。
3. Workspace / Project Phase 0 至 Phase 3 已完成；浏览器级跨 Project 自动化隔离和 Artifact ownership 尚未完成。
4. Agent / Subagent 配置版本、评估指标和 Secret 轮换尚未形成平台能力。定时任务第一批（到点执行 + 应用内通知）已在线。
5. Electron 正式签名、公证、Windows 真机、`userData` 迁移和自动更新尚未完成。
6. 群聊 V2 第一版已落地。真并行、时间旅行、群事件进 Trace 仍未做。

## 核心入口

- `backend/src/server/routes/chat/`：Chat、Event Stream V3、恢复和 TeamRun。
- `backend/src/agents/factory.py`：AgentScope Agent / Model / Toolkit 构造入口。
- `backend/src/subagents/`：Subagent 定义、权限、上下文和执行。
- `backend/src/memory/`：记忆准入、检索、提炼和 Wiki。
- `backend/src/storage/database.py`：SQLite schema 和存储访问。
- `frontend/src/pages/ChatPage.tsx`：Chat 主工作台。
- `frontend/src/features/group-studio/`：群聊工作室独立 pane。
- `backend/src/group_studio/`：群聊工作室领域与 `/api/group-studio`。
- `frontend/src/pages/AgentsPage.tsx`：持久 Agent 管理。
- `frontend/src/hooks/useEventStream.ts`：RuntimeFrame SSE 订阅。
- `frontend/src/features/runtime-stream/`：Block Tree 协议、状态和 renderer。

完整文档分类见 `docs/README.md`，待办只维护在 `docs/project-todo.md`。
