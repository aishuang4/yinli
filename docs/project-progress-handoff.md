# Agent Workstation 项目进展与协作交接

> 更新时间：2026-06-16
>
> 本文档用于团队协作交接，帮助新同事快速理解当前项目做到哪里、如何启动、如何测试、接下来改什么。任务排期以 `docs/project-todo.md` 为准，本文更偏项目现状和协作入口。

## 1. 项目定位

Agent Workstation 是一个基于 AgentScope 2.0 的多 Agent 协作工作台，目标是让用户可以在前端配置 Agent、Team、MCP、Skills、知识库 HTTP 工具、模型供应商和记忆系统，并在 Chat 中运行单 Agent 或 Team 协作任务。

当前项目已经从原型阶段进入“功能闭环 + 稳定性补强”阶段。核心链路已经可用：

- 配置 Agent
- 配置 Team
- 注册 MCP / Skills / HTTP 知识库工具
- 在 Chat 中选择单 Agent 或 Team 运行
- 展示工具调用、Skills 使用、Team worker 协作、HITL 审批
- 将会话、Agent、Team、Memory、Trace 等数据持久化到 SQLite
- 查看运行轨迹和测试沙盒

## 2. 技术栈

### 后端

- Python 3.11
- FastAPI
- SQLite
- AgentScope 2.0
- `uv` 管理后端运行环境

核心目录：

- `backend/src/server/main.py`：FastAPI 入口和路由注册
- `backend/src/server/routes/`：API 路由
- `backend/src/agents/factory.py`：AgentScope Agent / Model / 工具运行时创建
- `backend/src/storage/database.py`：SQLite 表结构和存储访问
- `backend/src/tools/knowledge_base_search.py`：HTTP 知识库工具运行实现

### 前端

- React 19
- TypeScript
- Vite
- `@agentscope-ai/design`
- Ant Design
- Zustand

核心目录：

- `frontend/src/App.tsx`：页面路由和主题入口
- `frontend/src/pages/ChatPage.tsx`：Chat 主页面
- `frontend/src/pages/AgentTeamPage.tsx`：Agent / Team 管理
- `frontend/src/pages/MCPPage.tsx`：MCP 管理
- `frontend/src/pages/SkillsPage.tsx`：Skills 管理
- `frontend/src/pages/TracePage.tsx`：运行轨迹
- `frontend/src/pages/SandboxPage.tsx`：测试沙盒
- `frontend/src/features/agents/AgentEditor.tsx`：Agent 编辑器
- `frontend/src/hooks/`：前端 API hooks 和 SSE 事件流
- `frontend/src/themes/global.css`：全局视觉风格

## 3. 当前已完成能力

### 3.1 Agent 管理

已完成：

- Agent CRUD
- Agent 持久化到 SQLite
- Agent 导入 / 导出
- 模型配置：主模型、降级模型链、temperature、top_p、max_tokens
- 兼容字段：`planConfig`、`callableAgents` 等历史字段仍可读写，但旧 Plan Runtime 与 Subagent Runtime 已于 2026-06-26 下线，前端不再展示对应配置入口
- 记忆注入开关
- MCP、Skills、HTTP 知识库工具选择
- 工具权限配置：
  - 只读
  - 信任工具
  - 破坏性操作确认
  - 每次询问
- 执行后端配置：
  - local
  - DockerWorkspace
  - Docker image
  - 是否持久化沙盒工作目录

### 3.2 Team 管理

已完成：

- Team CRUD
- Team 持久化到 SQLite
- Team 导入 / 导出
- 内置 Team 模板
- 从已有 Team 保存为模板
- Team 成员可编辑
- 成员执行模式：
  - parallel
  - depends_on
- 成员依赖关系
- handoff prompt
- Chat 中支持单 Agent 和 Team 二选一运行
- Team worker 自动选择和人工确认
- Leader 汇总时会接收 worker 输出上下文

### 3.3 Chat

已完成：

- 单 Agent 对话
- Team 协作对话
- 历史会话
- SSE 流式输出
- Markdown 渲染
- 深度思考按钮
- 多模态图片输入
- Enter 发送，Shift + Enter 换行
- 输入法 composing 状态下 Enter 不误发送
- 工具调用前端反馈
- Skills 使用前端反馈
- Team worker 协作过程展示
- HITL 审批固定在输入框附近
- 消息 toolbar：
  - 复制文本
  - 消息时间
- 用户滚动历史时不强行自动滚动到底部
- 不在底部时显示居中的透明“滚动到底部”按钮
- 单 Agent / Leader / Worker 的工具调用历史可回显
- 工具调用显示去重展示名，例如避免 `KnowledgeBaseSearch_KnowledgeBaseSearch`
- 工具调用框固定高度，超过后内部滚动

### 3.4 MCP

已完成：

- MCP 注册
- 支持 URL、名称、描述、headers
- 拉取 MCP tools 并保存
- MCP 安装 / 卸载 / 启用 / 删除
- Agent 中可选择整个 MCP 或具体 MCP tool
- 删除 MCP 时会清理 Agent 已选择的关联工具

注意：

- 远程 MCP 注册依赖外网和服务端可访问性。
- 开发测试时避免随意用真实 token 注册不可信 MCP。

### 3.5 Skills

已完成：

- Skills 列表
- Skills 注册
- 支持标准 Skills zip 包导入
- 标准包结构：
  - `SKILL.md`
  - `scripts/`
  - `references/`
  - `assets/`
- zip 导入后会保留包内资源路径
- Agent 中可选择 Skills
- Chat 中在实际使用某个 Skill 时展示使用反馈
- 删除 Skills 时会清理 Agent 已选择的关联 Skills
- Chat SSE 稳定事件契约见 `docs/chat-sse-event-contract.md`，新增事件前必须先更新契约

### 3.6 HTTP 知识库工具

已完成：

- `/api/tools/knowledge-base` CRUD
- 前端 Tools 配置入口
- 可配置：
  - name
  - description
  - url
  - method
  - headers
  - body template
  - enabled
- Agent 中可选择 HTTP 知识库工具
- 运行时作为工具注入 Agent

典型用途：把已有的 HTTP 知识库检索接口包装成 Agent 可调用工具。

### 3.7 记忆系统

已完成：

- 每个 Agent 有主记忆文档
- Agent 可配置是否运行时注入记忆
- 记忆事实 CRUD
- 记忆事实支持：
  - pinned
  - active / outdated
  - access_count
  - last_accessed_at
- 记忆候选：
  - category
  - confidence
  - source session
  - reject reason
  - approve / reject
  - merge duplicates
- 记忆清理建议：
  - 重复记忆
  - 冲突记忆
  - 长期未使用记忆
- 单 Agent 和 Team 运行结束后可触发记忆候选提取 hook

当前策略：

- 自动提取候选记忆，但不直接写入长期记忆。
- 进入长期记忆需要用户审批。
- 记忆候选要求尽量简洁，只保留关键事实。

### 3.8 系统设置和模型

已完成：

- 系统设置页
- 模型供应商配置
- 每个供应商单独保存：
  - API key
  - base URL
  - 默认模型
  - fallback models
  - custom models
- Agent 选择模型时只关心模型，不直接关心供应商
- 只能选择系统设置中已配置供应商暴露的模型
- 供应商侧 prompt cache 使用信息展示：
  - cache 创建
  - cache 命中
  - 命中率
  - 估算节省 input tokens

### 3.9 运行轨迹 Trace

已完成：

- `run_events` SQLite 表
- Trace 页面
- Trace API
- 记录：
  - 模型调用
  - fallback 模型链尝试
  - 工具调用
  - Skills 使用
  - Team worker 状态
  - HITL_REQUIRED / HITL_RESOLVED
  - provider cache usage
  - workspace config
  - Bash 命令 stdout / stderr / exit-code metadata
  - Write / Edit 文件操作
  - shell 命令潜在文件变更提示

待补：

- Trace 页面更细的筛选能力。

### 3.10 测试沙盒 Sandbox

已完成：

- Sandbox 页面
- `/api/sandbox/cases` CRUD
- 保存单 Agent 测试用例
- 保存 Team 测试用例
- 可复用测试输入

待补：

- 自动断言
- 批量回归测试
- 测试结果历史记录

### 3.11 前端视觉和交互

已完成：

- 使用 `@agentscope-ai/design` 作为主要设计组件基础
- 黑 / 白主题已调整为低亮度版本
- 页面整体偏悬浮风格
- 容器卡片尽量无边框
- 列表项 / 成员卡片 / 工具卡片保留边框
- 抽屉、侧栏、卡片加入过渡动画
- 路由级懒加载
- 页面 skeleton
- Chat 长消息渲染性能优化

## 4. 本地启动

### 后端

```bash
cd /Users/aishuang/agent-workstation/backend
uv run uvicorn src.server.main:app --host 0.0.0.0 --port 8765 --reload
```

默认端口：`8765`

健康检查：

```bash
curl -sS http://localhost:8765/api/health
```

### 前端

```bash
cd /Users/aishuang/agent-workstation/frontend
PATH=/Users/aishuang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm run dev -- --host 0.0.0.0
```

默认端口：`5173`

访问：

- `http://localhost:5173/`
- 局域网访问时使用 Vite 输出的 Network 地址。

注意：

- 如果用 IP 地址访问 HTTP 前端，浏览器可能不认为它是 secure context。
- 项目已经避免直接依赖 `crypto.randomUUID()`，优先使用 `uuid`，防止 IP 访问时报错。

## 5. 测试方式

### 已验证过的测试

2026-06-16 已完成一轮整体测试：

```bash
cd /Users/aishuang/agent-workstation/backend
.venv/bin/python -m py_compile $(find src -name '*.py' -print)
```

结果：通过。

```bash
cd /Users/aishuang/agent-workstation/frontend
PATH=/Users/aishuang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm run lint
```

结果：通过。

```bash
cd /Users/aishuang/agent-workstation/frontend
PATH=/Users/aishuang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH npm run build
```

结果：通过。

构建仍有一个 Vite chunk 体积 warning，主要来自 icon/design 相关共享包，不影响运行。

### 后端 API smoke 覆盖

使用 FastAPI TestClient 跑过临时 SQLite smoke 测试，覆盖：

- `/api/health`
- Agents CRUD / import / export / guardrails
- Memory CRUD / access tracking
- Teams CRUD / import / export / templates
- Sandbox cases CRUD
- HTTP 知识库工具 CRUD
- Trace list / detail
- Settings model get / update

结果：全部通过。

### 当前测试缺口

- 后端没有完整 pytest 测试套件，`backend/test/test_example.py` 只是示例。
- 还没有端到端 UI 自动化测试。
- Team 并发 / 依赖执行、HITL、真实模型调用、真实 MCP 注册依赖外部服务，当前主要靠人工测试和 smoke 测试。

建议下一步补：

- 后端 API pytest suite
- Chat SSE 事件解析单元测试
- Team execution graph 测试
- Memory hook 测试
- Playwright E2E 测试

## 6. 数据存储

当前使用 SQLite 作为主存储，位置默认在：

```text
backend/data/agent_workstation.db
```

主要表：

- `agents`
- `teams`
- `team_members`
- `mcp_servers`
- `skills`
- `chat_sessions`
- `chat_messages`
- `run_events`
- `memory_facts`
- `memory_events`
- `memory_candidates`
- `json_store`

说明：

- 旧 JSON 文件迁移逻辑仍保留，开发阶段用于兼容已有数据。
- 当前处于开发阶段，如结构变化较大，可以清空开发数据库重新生成。
- 正式上线前需要补数据库 migration 策略。

## 7. 协作规则

- 不要每次小改都提交 Git。
- 完成一个较完整功能或阶段性修复后再提交。
- 不要随意删除用户本地数据。
- 不要使用 `git reset --hard` 等破坏性命令。
- 如果改动数据结构，更新：
  - `backend/src/storage/database.py`
  - 相关 route model
  - 前端 `types`
  - `docs/project-todo.md`
  - 本文档相关部分
- 如果新增页面，需要同步：
  - `frontend/src/App.tsx`
  - `frontend/src/layouts/ActivityBar.tsx`
  - 样式和 loading 状态
- 如果新增运行时事件，优先写入 `run_events`，再考虑前端展示。

## 8. 已知风险和待办

### 高优先级

- Trace 页面筛选能力还不完整。
- 后端缺少正式 pytest 测试套件。
- Team 复杂 DAG 执行需要更多测试，尤其是 worker 跳过、依赖等待、leader 汇总上下文。
- 真实 MCP / 真实 Skills / 真实 HTTP 工具调用需要更多异常场景测试。

### 中优先级

- 前端 shared chunk 偏大，需要继续做 icon / design 包按需优化。
- Sandbox 需要自动断言和批量回归。
- Settings 中 provider pricing 未配置时，只能展示 token 级节省估算，不能给价格估算。
- Memory decay 和自动重组仍需要用户审批流程设计。

### 长期方向

- Milvus 或向量数据库集成
- Agent / Team 模板市场
- 多语言 UI
- 桌面端自动更新
- 正式数据库 migration
- 权限与审计体系增强

## 9. 新同事建议上手顺序

1. 先跑起来后端和前端。
2. 在系统设置中配置一个可用模型供应商。
3. 创建一个简单 Agent。
4. 在 Chat 中跑单 Agent。
5. 创建一个 Team，至少包含 Leader + 2 个 Worker。
6. 在 Chat 中跑 Team，观察 worker 协作展示和 Trace。
7. 注册一个 HTTP 知识库工具，注入到 Agent 测试工具调用。
8. 导入一个标准 Skills zip 包，注入到 Agent 测试 Skills 调用。
9. 看 `docs/project-todo.md` 选择下一个任务。

## 10. 推荐人工测试案例

### 单 Agent 记忆

输入：

```text
你叫张三，请记住这个偏好：以后回答我时默认用简洁中文。
```

预期：

- Agent 正常回答。
- 运行结束后产生记忆候选。
- 用户审批后进入该 Agent 记忆。
- 下一轮运行可注入这条记忆。

### Team 并行 + 依赖

输入：

```text
请团队协作完成一个产品需求分析。调研 Worker 和风险 Worker 可以并行，方案 Worker 必须等待调研 Worker 输出后再设计方案，最后 Leader 汇总。
```

预期：

- 无关 Worker 可以被跳过或不选。
- 并行 Worker 同时执行。
- 依赖 Worker 等待上游输出。
- Leader 汇总时引用 worker 结果。
- Chat 侧边栏能看到 worker 状态变化。
- Trace 中能看到 worker run events。

### HITL

输入：

```text
请创建一个测试文件 worker-hitl-demo.md，内容为 Worker HITL test。如果需要写文件，请先请求人工确认。
```

预期：

- 审批请求出现在输入框附近。
- 用户批准后继续执行。
- HITL_REQUIRED / HITL_RESOLVED 写入 Trace。

### HTTP 知识库工具

输入：

```text
请调用知识库检索工具查询 agent-workstation 的记忆系统设计，并基于结果总结。
```

预期：

- 工具调用单独显示。
- 工具名称不重复拼接。
- 输入和输出可滚动、可复制。
- 历史会话中工具调用仍能回显。

## 11. 文档关系

- `docs/agent-workstation-design.md`：产品和系统需求说明
- `docs/agent-workstation-frontend-design.md`：前端设计说明
- `docs/project-todo.md`：当前任务排期和状态
- `docs/project-progress-handoff.md`：当前项目进展与协作交接
