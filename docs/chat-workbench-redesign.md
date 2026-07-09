# Chat Workbench 前端改造设计方案

> 状态：设计约束草案。本文用于约束 Chat 首页与全局入口改造，避免后续实现偏离当前系统能力边界。

## 1. 设计目标

Chat 页面从传统业务后台布局改为深色桌面工作台风格，让用户进入应用后首先感知到“正在和 Agent 协作完成任务”，而不是在配置系统里找入口。

核心目标：

- Chat 是主体验，其他模块是按需打开的控制能力。
- 历史对话、当前任务、输入区、运行状态要服务 Chat，不反向挤压 Chat。
- 保持现有系统能力分布，不因为布局参考图改变业务模块边界。
- 先做 Chat 壳层和入口形态，不扩散重做全部业务页面。

## 2. 重要边界

本方案里的布局图只表达空间关系、视觉气质和交互入口，不代表实际工具、能力或数据域的重新分布。

实际功能分布仍以当前系统为准：

- Agent 配置仍归 Agents 模块。
- Team 配置仍归 Teams 模块。
- Subagent 定义、调用历史、详情仍归 Subagents 模块。
- Skill 管理仍归 Skills 模块。
- MCP 服务和工具配置仍归 MCP 模块。
- Memory 设置、审计、Wiki 仍归 Memory 模块。
- Trace 运行轨迹仍归 Trace 模块。
- Sandbox / Workflow / Settings 等现有页面保持各自领域边界。
- Chat 只消费这些模块已经暴露给会话运行时的选择、状态和结果，不在 Chat 内复制完整配置页。

## 3. 总体布局

第一版采用两栏工作台：

- 左侧：历史会话 / 项目会话列表。
- 中间：Chat 主阅读区和底部输入控制台。

整体视觉参考 Codex 桌面风格：深色背景、低对比边界、紧凑列表、居中的对话阅读区、底部输入控制台。

### 3.1 左侧历史区

左侧历史区属于 Chat，不是全局导航。

承载内容：

- 新对话。
- 搜索会话。
- 最近会话列表。
- 项目分组下的会话列表。
- 当前会话高亮。

不承载内容：

- Agents、Teams、Skills、MCP 等全局功能导航。
- 复杂筛选、批量管理、系统设置。
- Agent / Team / Subagent 详情编辑。

交互规则：

- 桌面宽屏可以常驻。
- 窄屏或用户收起后可变成覆盖式历史面板。
- 打开历史面板时不推动中间 Chat 阅读区。

### 3.2 中间 Chat 主区

中间区域是主体验，优先保证阅读和输入。

承载内容：

- 当前会话标题。
- 用户消息。
- Agent 回复。
- RunState 投影后的过程块。
- Team / Subagent 的轻量运行状态。
- HITL 审批和用户追问。
- 最终答案、生成文件、错误提示。
- 底部输入控制台。

布局规则：

- 对话内容居中，推荐最大宽度 860-960px。
- 输入区跟随主阅读区宽度，不铺满整个窗口。
- 运行过程只展示对用户有意义的摘要，不在 Chat 主区堆 Trace 级明细。
- Raw SSE、raw JSON、完整工具参数等技术细节放 Trace 或对应模块详情页。

### 3.3 工作台菜单页

右上角设置入口进入一个独立的工作台菜单页。该页面左侧是功能菜单，左上提供返回 Chat 的入口；右侧展示被选中的现有功能页面。

菜单展示现有模块，但不包含 Chat：

- Agents
- Teams
- Subagents
- Tools
- Skills
- MCP
- Memory
- Trace
- Runs
- Sandbox
- Workflow
- Settings

点击后进入对应现有页面或切换到对应工作空间。功能名称、权限和页面能力以当前系统实现为准，不因为菜单页改变业务语义。

## 4. 信息架构原则

### 4.1 Chat 只做运行面

Chat 页面负责发起任务、承载流式过程、展示结果、处理用户追问和审批。

Chat 不负责完整配置管理：

- 不在 Chat 内编辑 Agent 全量配置。
- 不在 Chat 内编辑 MCP server。
- 不在 Chat 内管理 Skill 生命周期。
- 不在 Chat 内展示 Subagent 全量调用历史。

Chat 可以提供轻量选择：

- 当前 Agent / Team。
- 模型或运行模式摘要。
- 权限模式摘要。

### 4.2 模块页保持管理能力

非 Chat 页面仍可以保持管理型布局，因为它们承担配置、审计、列表管理和详情编辑。

后续可以逐步统一深色视觉，但不要求第一阶段重做所有页面。

### 4.3 运行细节分层展示

同一份运行数据按层级展示：

- Chat：用户可理解的当前状态和最终结果。
- Trace：完整事件、工具参数、错误细节、审计信息。
- 领域页面：对应模块的定义、配置、历史和统计。

## 5. 第一阶段改造范围

第一阶段只做 Chat 工作台壳层，避免范围扩散。

包含：

- 新增 Chat Workbench 布局容器。
- 将 Chat 从现有全局 `AppLayout` 的常驻顶部栏 / ActivityBar 中解耦。
- 左侧会话历史区适配当前 `ChatSession` 数据。
- 中间复用现有 Chat 消息流、RunState 投影、输入发送逻辑。
- 右上角设置按钮进入工作台菜单页。
- 工作台菜单页第一版只承载现有页面入口，不重做页面内容，不包含 Chat。

不包含：

- 重做 Agents / Teams / MCP / Skills / Memory 页面。
- 改变后端 Chat Runtime。
- 改变工具、Skill、MCP、Subagent 注入语义。
- 新增新的 runtime.kind。
- 重做 Trace 数据结构。

## 6. 实现落点建议

建议新增或拆分的前端结构：

```text
frontend/src/pages/ChatPage.tsx
frontend/src/features/chat-workbench/
  ChatWorkbenchLayout.tsx
  ChatHistoryRail.tsx
  ChatTopBar.tsx
  ChatInputDock.tsx
  WorkbenchMenuPage.tsx
  workbenchNavigation.ts
```

迁移原则：

- `ChatPage.tsx` 保留运行编排和现有 Chat 状态，逐步把视觉壳层拆到 `features/chat-workbench/`。
- `features/chat/` 继续负责消息展示、过程块、Team/Subagent/HITL 展示。
- 工作台菜单页只调用 `useAppStore().setView()` 或切换现有 workspace，不复制页面逻辑。
- 左侧历史区复用现有 session 加载、选择、新建能力。

## 7. 验收标准

视觉验收：

- Chat 默认打开时不再像传统业务后台。
- 用户第一眼看到的是当前会话、消息和输入区。
- 左侧历史区不会让主阅读区变窄。
- 设置入口出现后，功能入口清晰但不常驻干扰 Chat。

功能验收：

- 新建会话、切换会话、刷新恢复正常。
- 单 Agent 对话正常。
- Team 对话正常。
- Subagent 委托状态和最终结果正常显示。
- HITL 审批、AskUser 追问正常。
- 生成文件、错误提示、运行中状态正常。
- 从工作台菜单页能进入现有模块页面，且左上返回可以回到 Chat。

工程验收：

- 不改 Chat SSE 稳定契约，除非先更新 `docs/chat-sse-event-contract.md`。
- 不改变后端 Runtime 分发。
- 不把领域模块配置逻辑复制进 Chat。
- `npm run build` 通过。
- 高风险交互完成手动冒烟。

## 8. 后续阶段

第二阶段可以考虑：

- 工作台菜单页搜索命令。
- 非 Chat 页面逐步统一深色工作台视觉。
- Chat 历史区支持项目分组、归档、快速搜索。
- 用 Playwright 补 Chat 工作台基础视觉和交互冒烟。

这些都不影响第一阶段目标：先把 Chat 主体验从传统后台布局中解放出来。
