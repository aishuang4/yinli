# 工具管理模块规划

> 状态：第一阶段实施中。目标是把 MCP、通用 HTTP 工具、系统内置工具统一放入工具管理中心，同时保持后端业务边界清晰。

## 模块定位

工具管理是 Agent Workstation 的一级能力入口，用于管理 Agent 可调用的工具资源。它不替代 Skills：Skills 是能力包和操作手册，Tools 是可执行工具。

第一版包含三个子界面：

- **MCP**：协议化工具服务器，负责注册 MCP Server、发现工具、启用/禁用和删除。
- **HTTP 工具**：把传统系统 REST API 快速封装成 Agent 可调用工具。
- **系统工具**：展示内置工具能力、风险等级和默认注入策略，例如 Read、Write、Bash、AskUser。

## 后端边界

HTTP 工具使用独立表 `http_tools`，不再挂在知识库工具概念下。知识库检索只是 HTTP 工具的一种模板。

第一阶段新增接口：

- `GET /api/tools/http`
- `POST /api/tools/http`
- `PUT /api/tools/http/{tool_id}`
- `DELETE /api/tools/http/{tool_id}`
- `GET /api/tools/system`

旧接口 `/api/tools/knowledge-base` 暂时保留，保证现有 Agent 选择和历史配置不被破坏。

## HTTP 工具字段

核心字段：

- 名称、描述、分类、启用状态
- Method、URL
- Auth 类型与配置
- Headers、Query 模板、Body 模板
- 参数说明和参数 schema
- 返回处理模式和返回路径
- 只读标记
- 审批策略

后续会增加调试请求、响应提取规则、调用耗时记录和 Agent 注入。

## 前端设计

新增 `ToolsPage`：

- 顶部 Tab：MCP / HTTP 工具 / 系统工具
- MCP 复用现有 MCP 页面能力
- HTTP 工具使用卡片列表和编辑弹窗
- 系统工具使用只读卡片展示

视觉风格沿用 MCP / Skills 页面：卡片式列表、标签、固定操作区、抽屉或弹窗编辑。

## 后续阶段

1. 将现有知识库 HTTP 工具迁移为 HTTP 工具模板。
2. Agent 高级设置中把“知识库工具”升级为“HTTP 工具”。
3. 运行时新增 `ConfiguredHttpTool`，每个 HTTP 工具以独立工具名注入 Agent。
4. Trace 展示 HTTP 工具 URL、方法、状态、耗时、响应摘要。
5. 接入统一资源权限系统，支持 HTTP 工具单项覆盖策略。

