# frontend

`frontend/` 里的 React 应用。上层：[../AGENTS.md](../AGENTS.md)。

## 先读

- [src/features/chat/AGENTS.md](src/features/chat/AGENTS.md) — Chat 输入、消息和预览。
- [src/features/runtime-stream/AGENTS.md](src/features/runtime-stream/AGENTS.md) — RuntimeFrame 和 Block Tree。
- [src/features/group-studio/AGENTS.md](src/features/group-studio/AGENTS.md) — 群聊工作室面板。

## 职责

这棵目录渲染工作台并调用 API。它不构造 Agent，也不写 SQLite。

## 布局

- `src/pages/` — 路由页面。页面负责数据、布局和导航。
- `src/features/<domain>/` — 领域界面：列表、详情、表单和局部状态。
- `src/components/` — 共用界面。表单字段在 `src/components/form/`。
- `src/hooks/` — API 和 SSE hooks。
- `src/stores/` — Zustand。产品档来自 product-features store。
- `src/types/` — 共用类型。用 `import type` 导入。
- `@/` 指向 `src/`。

依赖向下：`pages` → `features` → `components` → `hooks` / `types` / `utils`。组件不导入页面。hook 不导入页面。

## 命令

在 `frontend/`：

```bash
npm install
npm run dev
npm test
npm run lint
npm run build
npm run electron:dev
npm run electron:compile
npm run electron:pack
npm run electron:build:mac
npm run electron:build:win
```

交出前端改动前跑 `npm run build`。当前规则允许时再跑 `npm run lint`。在浏览器里验证改过的流程：创建、编辑、删除、刷新。

## 组件

- 单个 `.tsx` 超过大约 500 行时，拆到 `features/`、`components/` 或局部子组件。
- 列表项、详情、筛选、弹窗和抽屉不放在页面文件里。
- 可复用的领域界面放 `src/features/<domain>/`。通用界面放 `src/components/`。
- 每个组件 prop 都要有类型。不用 `any`。
- 页面文件保留数据 hook、顶层布局状态、传下去的 props、刷新、错误和导航。
- feature 组件保留自己的列表、卡片、表单段落，以及只属于自己的状态。
- 优先用 `@agentscope-ai/design`。没有合适组件时再用 Ant Design 或自定义界面。
- 业务表单用 `src/components/form/` 里的 `TextField`、`SelectField`、`TextAreaField`、`DateField`、`ToggleField` 以及其他从那里导出的字段。
- 业务页面不直接写原生 `input`、`textarea`、`select`，也不直接把设计库的 `Input`、`Select`、`DatePicker`、`Switch`、`Slider`、`InputNumber` 当成表单字段。
- 导入本地文件用 `src/components/FileImportButton.tsx`。
- 目录筛选复用 `src/components/form/CatalogFilterBar.tsx`。领域筛选放在该领域的 feature 里。
- 重复出现的可点击行做成组件。单独的 tab 或卡片可以用 `<button>`。
- 高频区域用按钮级 loading 或局部骨架。
- 少用的设置放进抽屉、弹窗或折叠区。
- 长文本、工具输出和日志限制高度，并在框内滚动。
- 图标用 `lucide-react`。按钮里的图标居中。
- 大页面用 `React.lazy` 做路由级拆包。
- 长列表分页、虚拟滚动，或设置 `content-visibility: auto`。
- 删除一行时只去掉这一项。不为这一次删除重载整个列表。

## 状态

- 服务端数据走 hook，例如 `useAgents`。
- 删除、启用或小编辑成功后，更新本地那一条记录。
- 只有多个区域共用结果，或服务端做了合并时，才用响应替换本地状态。
- 派生数据用 `useMemo`。不用 `useEffect` 加 `setState` 保存可算出的值。
- 不保留大对象的第二份副本。
- 用对象映射视图，像 `App.tsx` 的 `VIEW_PAGES`。不要对视图名写 switch。
- 在 `useLayoutEffect` 里把主题同步到 `data-theme`。
- 一次性初始化只做一次。不要在派生渲染里重复做。

## Chat 展示边界

- 新的展示能力在 [src/features/runtime-stream/AGENTS.md](src/features/runtime-stream/AGENTS.md) 注册 `Block.kind`。
- 整理非 Chat 表单时不改 Chat 行为。`pages/ChatPage.tsx` 和 `features/chat/` 按自己的节奏改。
- 加表单控件前扫描非 Chat 界面：

```bash
rg -n "<(Input|Select|DatePicker|Switch|Slider|InputNumber|textarea|input|select)\b|from ['\"]antd['\"]" \
  frontend/src/pages frontend/src/features frontend/src/components \
  -g '*.tsx' -g '!frontend/src/pages/ChatPage.tsx' -g '!frontend/src/features/chat/**'
```

命中 `components/form/` 和 `FeedbackBridge` 以外的地方时，要说明原因，或把控件收进共用表单层。

## 同步

- Chat SSE 的改动同时更新契约、后端发送和本目录的读取。
- 产品档改动使用接口返回的档。不要再加一份写死的离线视图列表。
