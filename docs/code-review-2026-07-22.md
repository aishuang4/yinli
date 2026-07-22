# 引力 YINLI 项目审查报告

> 审查日期：2026-07-22
> 审查范围：backend/src（~35.7k 行 Python）、frontend/src（144 个 TS/TSX 文件）、electron、docs
> 基线：后端 346 passed / 4.6s；前端 lint 干净、tsc 通过、`any` 使用 0；`TODO/FIXME` 0

## 总体评价

工程质量在原型阶段项目中属上乘。测试基线扎实（比 handoff 文档记录的 329 有增长），前端类型安全维护良好，文档体系（AGENTS.md + docs/）详尽且与代码同步。架构分层清晰：Chat 链路 readiness → intent → dispatch → runtime → completion 职责切分明确，旧 Plan / Subagent 独立 Runtime 已彻底下线并有回归守护。成熟度自评 79% 可信。

主要问题集中在**安全边界**和**大文件职责膨胀**两处。

## 问题清单速览

| 级别 | 编号 | 问题 | 位置 |
| --- | --- | --- | --- |
| 严重 | 1 | 后端监听 `0.0.0.0` + 无认证 + 暴露命令执行 API | `backend/src/config/settings.py:20` |
| 中等 | 2 | WebFetch DNS rebinding / TOCTOU SSRF 缺口 | `backend/src/tools/web_fetch.py:125` |
| 中等 | 3 | 文件作用域非硬沙箱，绝对路径可越界 | `backend/src/tools/scoped_paths.py:13` |
| 中等 | 4 | GuardedBash 黑名单为弱防御（子串匹配） | `backend/src/agents/factory.py:227` |
| 中等 | 5 | 超大文件职责膨胀 | `database.py` / `projector.py` / `leader_stream.py` / `ChatPage.tsx` |
| 轻微 | 6 | 同步 sqlite3 在 async 路由中直接调用 | `backend/src/storage/database.py` |
| 轻微 | 7 | Vite 968KB 大 chunk | `frontend/dist/assets/cn-*.js` |
| 轻微 | 8 | Electron 未见显式 CSP | `electron/main.ts` |

## 处理进度（2026-07-22）

- [x] 严重 1：默认监听改为 `127.0.0.1`，并增加不信任代理头的本机来源 ASGI 边界；即使误绑定全部网卡，局域网请求也会被拒绝。
- [x] 中等 2：WebFetch 使用固定 IP transport，DNS 校验结果直接用于 TCP 连接，并保留原始 Host 与 TLS SNI；每次重定向重新解析、校验和固定。
- [x] 中等 3：Read / Write / Edit / Grep / Glob 统一执行真实路径 scope 校验，拒绝绝对路径、父目录和符号链接越界；相对工作目录与 filesystem scope 分离。
- [x] 中等 4：代码、工具说明、架构文档和 Agent 设置界面均明确 Shell 黑名单是提示级误操作防护，强隔离依赖 DockerWorkspace 与权限审批。
- [ ] 中等 5（进行中）：已完成八轮增量拆分。前三轮已拆分最初四个核心文件；第四轮处理后台 Run、前端事件流与 Skill 包职责；第五轮把 Chat Run、Workflow、Memory 三个 SQLite 表族迁出 `database.py`；第六轮迁出 Workspace / Project / Project Resource Binding 与 Skill Evolution；第七轮迁出 JSON Namespace 兼容存储与 Agent / MCP / HTTP Tool / Skill 资源目录；第八轮继续迁出 Skill Run、运行轨迹事件和 Subagent Invocation 审计。核心文件均已收缩，但仍需按领域持续拆分，不一次性重写。

## 严重问题

### 1. 后端监听 `0.0.0.0` + 无认证 + 暴露命令执行 API

`backend/src/config/settings.py:20` 默认 `host = "0.0.0.0"`，端口 8765；`backend/src/server/main.py` 全局无任何认证依赖（`Depends(auth)` / Bearer / token 均无）。而 API 可触发 Bash 执行、文件读写。

虽然 Electron 端只连 `127.0.0.1:8765`（`electron/main.ts:85`），但服务器绑定了所有网卡——在共享 WiFi / 办公网 / 咖啡厅网络下，**同网段任意主机可直接 POST 调用后端执行任意命令**。CORS 白名单不构成防护（CORS 只约束浏览器，不约束直接 HTTP 请求）。

**建议**：本地桌面场景默认绑定 `127.0.0.1`；如需 `0.0.0.0` 应显式配置并加本地 token 校验。

## 中等问题

### 2. WebFetch 存在 DNS rebinding / TOCTOU SSRF 缺口

`backend/src/tools/web_fetch.py:125-126`：`_validate_target()` 用 `getaddrinfo` 校验 IP 为公网后，第 126 行 httpx 发起请求时**独立再解析一次 DNS**，两次解析结果可能不同。恶意 DNS 首次返回公网 IP 通过校验、二次返回 `169.254.169.254`（云元数据）或内网地址即可绕过。

其余 SSRF 防护（拒私网 / 凭据、逐跳重定向校验、大小限制、非文本拒绝）都做得很好，就差 IP 绑定这一步。

**建议**：校验后将解析到的 IP 固定传给 httpx（自定义 transport / `resolver`），使校验与连接使用同一地址。

### 3. 文件作用域不是硬沙箱，绝对路径可越界

`backend/src/tools/scoped_paths.py:13` 对绝对路径原样返回，`scoped_read.py:38` 的 `_resolve_file_path` 直接透传。`filesystem_scope` 实际只做「相对路径解析基点 / 默认工作目录」，并不拦截 `/etc/passwd` 这类 Project 边界外的绝对路径读取。这与文档宣称的「Project root 与 Agent scope 取交集」的隔离模型有落差。

**建议**：在 scoped_read / write / edit 落地前统一做 `Path.resolve()` 后 `is_relative_to(scope)` 边界校验并拒绝越界。

### 4. GuardedBash 黑名单为弱防御（子串匹配）

`backend/src/agents/factory.py:227-228` 用 `blocked in command` 子串匹配，可被 `${IFS}`、引号拆分（`r""m`）、base64 解码管道、别名等轻易绕过。考虑到本地优先、Agent 由用户自控的定位，这属于纵深防御问题而非高危，但不应被当作可靠的命令拦截手段依赖。

**建议**：文档明确其为「提示级防护」，真正隔离依赖 DockerWorkspace。

### 5. 超大文件职责膨胀

- `storage/database.py`：3772 行降至 1889 行
- `chat/event_hub/projector.py`：2247 行降至 671 行
- `chat/leader_stream.py`：1397 行降至 928 行
- 前端 `pages/ChatPage.tsx`：1419 行降至 904 行
- `chat/background_runs.py`：1193 行降至 803 行
- `routes/skills.py`：998 行降至 818 行
- 前端 `hooks/useEventStream.ts`：974 行降至 354 行

这些是维护热点，改动风险和 review 成本高。

各轮采用兼容入口和纯转换模块渐进拆分，已新增以下职责模块：

- `storage/plan_records.py`：Plan / PlanStep / PlanEvent 的 SQLite 领域读写；`database.py` 保留公开兼容函数。
- `storage/chat_run_records.py`：Chat Run、原始 SSE 事件与 RunState 快照的 SQLite 领域读写。
- `storage/workflow_records.py`：Workflow Run、节点、边、事件和产物的 SQLite 领域读写。
- `storage/memory_records.py`：长期记忆节点与召回反馈的 SQLite 领域读写。
- `storage/workspace_project_records.py`：Workspace、Project 与 Project 资源白名单的 SQLite 领域读写。
- `storage/skill_evolution_records.py`：Skill 自进化候选的写入、查询和状态流转。
- `storage/json_document_store.py`：轻量 JSON Namespace CRUD 与历史 JSON 文件兼容导入。
- `storage/resource_catalog_records.py`：Agent、MCP、HTTP Tool 与 Skill 资源目录的 SQLite 领域读写。
- `storage/skill_run_records.py`：Skill Run 的查询与状态写入。
- `storage/trace_records.py`：运行轨迹事件与 Subagent Invocation 的审计读写。
- `chat/final_answer_recovery.py`、`chat/plan_stream.py`：最终答案恢复与 Plan 流事件桥接。
- `chat/event_hub/generated_files.py`、`hitl_display.py`、`subagent_timeline.py`：产物归一化、HITL 展示和 Subagent 时间线转换。
- `chat/event_hub/display_events.py`、`process_timeline.py`、`runtime_projection.py`、`run_state_mutations.py`：Display Event 构建、过程时间线、Runtime Event 投影和 RunState 领域对象读写。
- `features/chat/ChatHistoryPanel.tsx`、`ChatComposer.tsx`、`clarificationHistory.ts`：会话历史 UI、输入区和 clarification 历史恢复。
- `chat/background_run_events.py`、`background_run_summary.py`：后台 Run 的 SSE/RunState Patch 编码与任务中心摘要。
- `hooks/eventStreamTypes.ts`、`runStateStreamAdapter.ts`：前端事件流公开类型与 RunState/DisplaySnapshot 状态适配。
- `routes/skill_packages.py`：Skill 标准包文件读写、ZIP 安全校验、导入导出和兼容诊断。

第八轮完成后验证：后端 `355 passed`，前端 lint 与生产构建通过。`database.py` 继续保留 SQLite 初始化、schema 创建和原公开函数签名，新增领域 Store 通过依赖注入复用当前连接、锁、序列化能力和动态默认配置，因此测试对 `DB_PATH`、`_initialized` 与运行时 Project root 的替换方式保持有效。`database.py`、`leader_stream.py`、`ChatPage.tsx`、`skills.py` 等仍需继续拆分，本项保持进行中。

**建议**：database.py 后续继续迁出 Chat Session / Message Store 与查询辅助，再评估抽取共享连接与 schema 管理层；leader_stream 按职责拆分 handler。

## 轻微 / 改进建议

### 6. database.py 同步 sqlite3 在 async 路由中直接调用

全库仅 2 处 async，`chat/send.py` 等 async 端点直接调同步 DB，无 `run_in_executor` / `to_thread`。当前测试快是因为本地 SQLite 极快，但慢查询会阻塞事件循环。桌面单用户场景可接受，若未来并发上升需包装到线程池。

### 7. Vite 968KB 大 chunk

`dist/assets/cn-*.js` 968KB（疑似中文语言包 / 依赖），页面本身已按路由懒加载分包（ChatPage 等独立 chunk），首屏影响可控。可考虑对该依赖做二次 manualChunks 拆分或按需加载。

### 8. Electron 安全基线正确但可加固

`contextIsolation: true` / `nodeIntegration: false` / 外链走系统浏览器（`setWindowOpenHandler` deny）都对，但**未见显式 CSP**。生产环境建议补 `Content-Security-Policy` 与 `will-navigate` 拦截，防渲染进程被注入内容导航到外部。

## 结论

代码质量、测试与文档纪律都在线，架构演进方向清晰。

- **最优先处理第 1 项**（监听地址 + 认证），它把一个本地工具变成了潜在的网络攻击面。
- **其次是第 2、3 项**（SSRF 与路径越界）。
- 其余为结构性技术债，可随迭代逐步偿还。

---

*说明：本报告为直接读代码验证的结论。审查过程中并行分派的三个深度子代理因结果投递异常未能返回，上述发现均经人工确认，未含未回传内容。*
