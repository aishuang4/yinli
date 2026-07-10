# Agent Workstation 开发规范

> 状态：正式生效。本文用于统一后续 React、Python、SQLite、Agent Runtime 和协作开发习惯。

## 1. 总体原则

- 优先保持现有架构一致，不为单个需求引入全新技术栈。
- 功能改动要小步闭环：实现、验证、必要测试、更新文档。
- 用户可见能力优先保证稳定性、可恢复、可追踪，再追求复杂交互。
- TODO、交接文档、代码注释默认使用中文；变量、类型、API 字段保持英文。
- 不在业务代码中硬编码真实 token、私有地址、用户密码。

## 2. 模块化与依赖规范

### 2.1 模块划分

- 代码必须按功能职责拆分为独立模块，避免把多个业务域混在同一个文件中。
- 一个模块只负责一个清晰职责，例如：
  - API 请求封装放 `hooks/` 或领域 service。
  - UI 展示放组件。
  - 类型定义放 `types/`。
  - 后端路由放 `routes/`。
  - AgentScope 运行时门面放 `agents/factory.py`，模型创建和运行时类型分别放 `agents/model_factory.py`、`agents/runtime_types.py`。
  - 工具实现放 `backend/src/tools/`。
- 当一个文件同时承担“请求、状态、渲染、业务规则、数据转换”多种职责时，应优先拆分。
- 模块边界要围绕业务域和变化原因设计，不按随意的工具函数堆积。

### 2.2 导入模块

- 使用 `import` 明确导入其他模块暴露的函数、类型、类或常量，避免复制粘贴重复代码。
- 共享逻辑必须沉淀为模块，而不是在多个页面或路由中重复实现。
- 前端优先使用路径别名或相对路径保持导入清晰；后端优先从 `src.*` 包路径导入。
- 类型导入优先使用 `import type`，减少运行时依赖。
- 模块只导出必要 API，不导出内部临时变量和实现细节。
- 不允许通过跨层级深挖内部文件来绕过模块边界；如果确实需要复用，应调整模块公开出口。

### 2.3 避免循环依赖

- 设计模块时必须避免循环依赖，例如 `A -> B -> A`。
- 前端页面不应被底层组件反向导入；组件不应依赖具体页面。
- 后端路由不应互相导入复杂业务逻辑；共享逻辑应下沉到 service/helper/runtime 模块。
- 类型定义、常量和纯函数可以下沉到独立模块，用来打破循环依赖。
- 如果出现循环依赖倾向，优先采用以下方式处理：
  - 抽出公共类型到 `types/`。
  - 抽出公共逻辑到 `helpers` / `runtime` / `service`。
  - 通过参数传入依赖，而不是模块顶层互相导入。
  - 将高层编排逻辑留在页面/handler，底层模块只做单一职责。

### 2.4 分层依赖方向

- 前端推荐依赖方向：
  - `pages -> features -> components -> hooks/types/utils`
  - `components` 不反向依赖 `pages`。
  - `hooks` 不直接依赖页面组件。
- 后端推荐依赖方向：
  - `routes -> runtime/service/helpers -> storage/tools/config`
  - `storage` 不反向依赖 `routes`。
  - `tools` 不直接依赖具体 API 路由。
  - `agents/factory.py` 是 AgentScope 运行时入口，其他模块不要复制运行时构造逻辑；运行时类型和模型降级链应复用 `agents/runtime_types.py`、`agents/model_factory.py`。
- Electron 推荐依赖方向：
  - `main/preload -> compiled frontend/backend entry`
  - 前端通过 preload 暴露的安全 API 访问桌面能力，不直接使用 Node API。

## 3. 前端 React / TypeScript 规范

### 3.1 组件化

- 页面只负责布局、路由级状态和业务编排；复杂 UI 必须拆成组件。
- 单个 `.tsx` 文件超过约 500 行时，优先拆分到 `features/`、`components/` 或局部子组件。
- 页面级 `.tsx` 原则上不承载大段列表项、详情面板、筛选栏、弹窗、抽屉内容；这些内容应按领域拆到 `frontend/src/features/<domain>/`。
- 可复用业务组件放 `frontend/src/features/<domain>/`。
- 通用 UI 组件放 `frontend/src/components/`，例如 `ToggleButton`、`MarkdownContent`。
- 组件 props 必须定义明确类型，避免 `any`。
- 列表项、卡片、弹窗、抽屉、表单段落都应组件化，避免页面内堆大段 JSX。
- 表单字段优先使用 `frontend/src/components/form/` 的统一封装；业务页面不要直接重复书写 label、help、error、input/select/textarea 布局。
- 复杂业务表单应抽到 `frontend/src/features/<domain>/`，页面只保留弹窗开关、请求编排和状态传递。
- 领域页面拆分后，页面文件应主要包含：
  - 数据 hook / API 调用编排。
  - 顶层 tab / layout 状态。
  - 传给 feature 组件的 props。
  - 跨面板刷新、错误提示和导航行为。
- Feature 组件应承接：
  - 列表、卡片、详情、时间线、筛选条、表单段落。
  - 局部派生状态和只影响本组件的 `useMemo` / `useState`。
  - 与自身 UI 强相关的展示 helper。

### 3.2 状态管理

- 服务端数据通过 hooks 封装，例如 `useAgents`、`useTeams`、`useMcp`。
- 删除、启用、局部编辑成功后优先本地更新，不要无脑刷新整个模块。
- 只有会影响多个区域、后端有复杂合并逻辑时，才使用接口返回值替换本地状态或重新拉取。
- 派生状态优先用 `useMemo`，不要用 `useEffect + setState` 保存可计算结果。
- 不把大型对象重复存多份，避免状态不同步。

### 3.3 UI 和交互

- 优先使用 `@agentscope-ai/design` 组件；没有合适组件时再用 Ant Design 或自定义。
- 通用表单封装内部也应优先组合 `@agentscope-ai/design`，原生表单控件只允许出现在基础组件或特殊交互组件内部。
- 业务页面禁止直接使用原生 `input`、`textarea`、`select` 或直接散写 `@agentscope-ai/design` 的 `Input` / `Select` / `DatePicker` / `Switch` / `Slider` / `InputNumber` 作为表单字段；应使用 `frontend/src/components/form/` 的 `TextField`、`SelectField`、`TextAreaField`、`DateField`、`ToggleField` 等封装。
- 需要本地文件导入时，优先使用 `frontend/src/components/FileImportButton.tsx`，由内部 `Upload.beforeUpload` 接管文件，不在页面中写隐藏原生 file input。
- 通用目录筛选栏优先复用 `frontend/src/components/form/CatalogFilterBar.tsx`；领域筛选栏放在对应 `features/<domain>/`。
- 非表单型可点击项（tab、卡片、列表项、时间线节点）可以使用 `<button>`，但重复出现的交互样式应抽成领域组件或通用选择项组件，不在页面里复制整段 className。
- 高频区域避免整块 loading 闪烁，使用按钮级 loading 或局部骨架。
- 主流程页面保持干净，复杂配置放到 Drawer / Modal / 折叠高级设置。
- 容器卡片尽量悬浮无边框；列表项、成员卡片、工具卡片保留边框。
- 长文本、工具输出、日志输出必须限制高度并允许内部滚动。
- 图标优先使用 `lucide-react`，按钮内图标必须居中。

### 3.6 前端整改基线

> 本节记录 2026-07-06 前端组件化整改后的基线，后续开发应保持该方向。

- 非 Chat 业务页面的表单控件已基本收口到 `frontend/src/components/form/`。新增表单时先查共享封装，不足再扩展封装层。
- 当前通用表单入口包括：
  - `FormField`、`FormSection`、`FormGrid`、`FormError`
  - `TextField`、`PasswordField`、`TextAreaField`
  - `SelectField`、`NumberField`、`SliderField`
  - `DateField`、`ToggleField`
- 文件导入统一走 `FileImportButton`；不要在页面里新增隐藏 file input。
- MCP / Skills 等目录页通用筛选优先复用 `CatalogFilterBar`。
- 大型页面当前拆分方向：
  - `MemoryPage` 只保留记忆数据装配和 tab 编排；Markdown、历史记忆、Skill 自进化队列、Wiki Vault 面板放在 `features/memory/`。
  - `TracePage` 只保留运行轨迹数据加载、选中态和事件分页；列表、详情、事件时间线、消息卡、工具列表放在 `features/traces/`。
  - `AgentEditor` 只保留 Agent 表单状态和保存编排；基础信息、模型高级设置、能力工具、Guardrails 和选择弹窗放在 `features/agents/sections/`。
- 新页面验收时至少扫描：

```bash
rg -n "<(Input|Select|DatePicker|Switch|Slider|InputNumber|textarea|input|select)\b|from ['\"]antd['\"]" \
  frontend/src/pages frontend/src/features frontend/src/components \
  -g '*.tsx' -g '!frontend/src/pages/ChatPage.tsx' -g '!frontend/src/features/chat/**'
```

- 该扫描在非 Chat 范围内只应命中共享基础封装层和必要桥接组件，例如 `components/form/`、`FeedbackBridge`。如果命中业务页面，需要解释原因或继续抽取。
- Chat 页面和 `features/chat/` 暂按独立节奏整改，非 Chat 整改不得顺手改 Chat 行为。

### 3.4 性能

- 长列表优先分页、虚拟滚动或 `content-visibility: auto`。
- Chat 流式输出期间减少不必要的状态更新和全量重渲染。
- 删除列表项只剔除对应 item，避免重新加载整个列表。
- 大型页面使用 `React.lazy` 做路由级拆包。
- 避免在 render 内创建昂贵计算；使用 `useMemo` 或提前归一化数据。

### 3.5 前端验证

- 提交前至少运行：
  - `cd frontend && npm run build`
  - `cd frontend && npm run lint`（如果当前规则允许）
- 涉及关键交互时，用浏览器手动验证：创建、编辑、删除、刷新后状态。

## 4. Python / FastAPI 规范

### 4.1 路由和分层

- API 路由按领域放在 `backend/src/server/routes/`。
- Chat 复杂链路继续按 `routes/chat/handlers`、`runtime`、`team`、`store` 拆分，避免单文件膨胀。
- AgentScope 运行时构造统一走 `backend/src/agents/factory.py`；类型定义和模型创建分别维护在 `runtime_types.py`、`model_factory.py`，不要重新塞回 factory。
- Plan Runtime 与 Subagent Runtime 已下线，重新设计前不要在旧兼容字段或旧事件上继续扩展。
- Chat 应用级状态统一使用 `RUNTIME_EVENT` 包装，事件类型定义集中在 `backend/src/server/routes/chat/event_types.py` 和 `frontend/src/types/chatEvents.ts`。
- 新功能必须定义自己的事件家族；Team 状态只能使用顶层 `TEAM_EVENT`，不得塞入通用 `RUNTIME_EVENT` 或单 Agent `DisplaySnapshot`。
- Chat SSE 事件新增或语义变更前，先更新 `docs/chat-sse-event-contract.md`，再改后端发送和前端消费。
- 工具实现放 `backend/src/tools/`，不要散落在路由里。
- 存储访问集中在 `backend/src/storage/database.py` 或领域封装函数里。

### 4.2 Pydantic 和类型

- 请求/响应模型使用 Pydantic，字段对外保持 camelCase 时用 alias。
- 内部 Python 字段使用 snake_case。
- 尽量补全类型标注，尤其是路由返回值、存储函数、运行时事件。
- 不直接返回未过滤的异常、token、headers 或密钥。

### 4.3 异步和阻塞

- FastAPI 路由可以是 async，但注意当前 SQLite 封装是同步 `sqlite3 + RLock`。
- 长耗时 Agent 运行必须走 SSE / 后台 Run，不要阻塞普通请求。
- 外部 HTTP、MCP、模型调用要考虑超时、异常和可恢复提示。

### 4.4 错误处理

- 用户可恢复错误返回清晰中文信息。
- 后端日志保留技术细节，前端只展示必要摘要。
- HITL、工具调用、模型 fallback、Agent 异常都应写入 `run_events`。

### 4.5 后端验证

- 提交前至少运行：
  - `cd backend && uv run pytest`
- 修改 AgentScope 运行时、HITL、工具调用、Team 协作，或重新设计记忆能力时必须补回归测试。

## 5. SQLite / 数据持久化规范

- 业务核心数据优先使用规范化表，不再新增零散 JSON 文件。
- Team Runtime 是协作运行状态的例外：按 `docs/team-runtime-redesign.md` 使用 file-first 设计，JSON/JSONL/Markdown 作为 TeamRun source of truth，SQLite 只保存 Chat 索引、RunState 和历史 `teamSnapshot`。
- `json_store` 只用于系统设置、轻量配置文档和确实不适合拆表的内容。
- 表结构演进继续使用 `_ensure_column()` / 初始化升级逻辑；正式上线前再评估迁移框架。
- 新表必须包含稳定主键、创建时间、更新时间；需要审计的数据保留操作记录。
- 删除数据时要明确是否级联清理 Agent/Team 关联字段。
- 历史运行数据原则上不可破坏，开发阶段确需清空数据库时必须明确说明。

## 6. Agent Runtime 规范

- Agent 配置变更后必须使运行时缓存失效。
- Tool / Skill / MCP 注入必须由 Agent 配置决定，不在 Chat 页面硬编码。
- 工具调用记录要同时满足：
  - 实时 UI 可见
  - 历史消息可回放
  - Trace 可审计
- HITL 暂停不代表 Run 完成；耗时和状态应延续到最终回复结束。
- Team Member 的输出、工具调用、思考、状态必须通过独立 `TEAM_EVENT` 记录，最终综合必须使用成功任务结果和 Mailbox。
- Skills 生命周期要明确：选择、注入、使用、等待用户、完成、失败，不能无限保持 active。

## 7. 测试规范

- 后端优先补单元/回归测试，覆盖运行时状态、HITL、工具历史、记忆更新等高风险逻辑。
- 前端暂以 `npm run build` + 手动验证为最低线；后续补 Playwright/E2E。
- Sandbox 测试用例用于验证 Agent/Team 行为，不替代代码测试。
- Bug 修复如果曾经复现过，尽量补一个最小回归测试。

## 8. Git 和协作规范

- 一个大功能或一组相关修复完成后再提交。
- 提交前检查：
  - `git diff --check`
  - 后端改动跑 `uv run pytest`
  - 前端改动跑 `npm run build`
- 提交信息使用简短英文前缀，例如：
  - `fix: preserve chat tool history`
  - `feat: add run center`
  - `docs: update platform todo`
- 不提交真实密钥、临时数据库、构建产物和本地日志。
- 修改 TODO 文档时使用中文，方便团队交接。

## 9. 文档规范

- 新增大功能前，先在 `docs/` 写设计或 TODO。
- 实现完成后更新 `docs/project-todo.md` 或交接文档。
- 面向开发者的规范写清楚目录、命令、约束和验收方式。
- 面向用户的说明避免暴露内部实现细节。

## 10. 建议后续补充的自动化

- 增加统一根目录命令：
  - `npm run check:frontend`
  - `uv run pytest`
  - `./scripts/check.sh`
- 增加格式化工具：
  - 前端 Prettier 或 ESLint format 规则
  - 后端 Ruff
- 增加前端 E2E：
  - Chat 发送
  - Agent 编辑
  - MCP/Skill 删除
  - 记忆候选忽略不整块刷新
