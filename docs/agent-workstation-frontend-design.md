# Agent Workstation 前端设计文档

> 更新：2026-06-26 旧 Plan Runtime 与 Subagent Runtime 已下线，本文中涉及计划模型配置、可调用子 Agent 的页面设计仅供历史参考，当前前端不展示这些入口。
>
> 基于 vercel-react-best-practices / web-design-guidelines / vercel-composition-patterns / tailwind-guidelines / css-modern-layouts 五大 skill 的综合设计
>
> 技术栈：Electron + React 19 + TypeScript + Spark Design + Spark Chat
>
> 创建时间：2026-06-06

---

## 1. 整体布局架构

### 1.1 应用布局（CSS Grid — Multi-panel IDE 模式）

采用 css-modern-layouts 的 Multi-panel IDE Layout 模式：

```
┌──────────────────────────────────────────────────────────┐
│  TitleBar (Electron 自定义标题栏)                          │  auto
├──────────┬────────────────────────────────┬──────────────┤
│          │                                │              │
│ Activity │         Main Content           │   Detail     │
│ Bar      │         Area                   │   Panel      │
│ (48px)   │                                │   (320px)    │
│          │   Chat / Agent Team /          │   可折叠      │
│ 图标导航  │   MCP / Skills / Settings     │   记忆/工具   │
│          │                                │   详情面板    │
│          │                                │              │
├──────────┴────────────────────────────────┴──────────────┤
│  StatusBar (模型状态 / 连接状态 / Token 用量)              │  auto
└──────────────────────────────────────────────────────────┘
  48px              1fr                        320px (可折叠)
```

**CSS 实现：**

```css
/* app-layout.css — 全局应用布局 */
.app-layout {
  display: grid;
  grid-template:
    "titlebar  titlebar  titlebar"  auto
    "activity  main      detail"    1fr
    "statusbar statusbar statusbar" auto
    / 48px 1fr var(--detail-width, 0px);
  height: 100vh;
  overflow: hidden;
  transition: grid-template-columns 200ms ease-out;
}

/* 当 Detail Panel 展开时 */
.app-layout:has(.detail-panel[data-open="true"]) {
  --detail-width: 320px;
}
```

### 1.2 Activity Bar（图标导航）

```
┌────┐
│ 💬 │  Chat（默认）
│ 🤖 │  Agents
│ 🔌 │  MCP
│ 📦 │  Skills
│ ⚙️ │  Settings
│    │
│    │  ← 底部
│ 🌓 │  Theme Toggle
└────┘
```

- 宽度固定 48px
- 图标按钮，带 tooltip
- 当前选中项高亮（accent color）
- 底部固定 Theme Toggle

### 1.3 Main Content Area

根据 Activity Bar 选择切换内容，使用 CSS Grid 内部的动态布局：

#### Chat 视图

```css
/* chat-view.css — 对话界面布局 */
.chat-view {
  display: grid;
  grid-template-rows: auto 1fr auto;
  height: 100%;
  min-height: 0; /* 防止 grid child 溢出 */
}

.chat-view__header { /* Agent 选择器 + 会话标题 */ }
.chat-view__messages { overflow-y: auto; } /* 可滚动消息区 */
.chat-view__input { /* 固定底部输入框 */ }
```

#### Agent Team 视图

```css
/* agent-team-view.css — Agent 管理布局 */
.agent-team-view {
  display: grid;
  grid-template-columns: 280px 1fr;
  height: 100%;
  gap: 0;
}

.agent-team-view__sidebar {
  /* Agent 列表，可滚动 */
  overflow-y: auto;
  border-inline-end: 1px solid var(--color-border);
}

.agent-team-view__content {
  /* Agent 详情编辑器 / Team 关系图 */
  overflow-y: auto;
  padding: 24px;
}
```

#### MCP / Skills 浏览器

```css
/* browser-view.css — MCP/Skills 浏览器 */
.browser-view {
  display: grid;
  grid-template-rows: auto auto 1fr;
  height: 100%;
}

.browser-view__search { /* 搜索框 */ }
.browser-view__filters { /* 分类/标签筛选 */ }
.browser-view__list {
  /* auto-fit 卡片网格 */
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
  padding: 16px;
  overflow-y: auto;
  align-content: start;
}
```

---

## 2. 组件架构（Composition Patterns）

### 2.1 组件分层策略

```
┌─────────────────────────────────────────────────┐
│  Pages (页面级)                                   │
│  ChatPage / AgentTeamPage / MCPPage / SkillsPage │
├─────────────────────────────────────────────────┤
│  Features (功能模块)                              │
│  ChatView / AgentList / AgentEditor / MemoryPanel│
├─────────────────────────────────────────────────┤
│  Widgets (可复用业务组件)                          │
│  AgentCard / ToolCallBlock / MemoryItem / MCPItem │
├─────────────────────────────────────────────────┤
│  UI Components (Spark Design 组件)                │
│  Button / Input / Card / Modal / Dropdown / Tag   │
├─────────────────────────────────────────────────┤
│  Primitives (基础元素)                            │
│  Icon / Badge / Avatar / Skeleton / Tooltip       │
└─────────────────────────────────────────────────┘
```

### 2.2 关键组件设计（Compound Components 模式）

#### ChatView（复用 Spark Chat）

```tsx
/**
 * ChatView — 对话视图，基于 @agentscope-ai/chat 的 ChatAnywhere 封装
 * 使用 Compound Component 模式，允许灵活组合 Header / Messages / Input
 */
function ChatView({ children }: { children: React.ReactNode }) {
  return (
    <ChatViewContext.Provider value={useChatState()}>
      <div className="chat-view">
        {children}
      </div>
    </ChatViewContext.Provider>
  )
}

/** 对话头部：显示当前 Agent 信息 + 会话切换 */
ChatView.Header = function ChatViewHeader() {
  const { currentAgent, sessionTitle } = useChatView()
  return (
    <header className="chat-view__header">
      <AgentBadge agent={currentAgent} />
      <h2 className="text-sm font-medium text-text-secondary">
        {sessionTitle}
      </h2>
    </header>
  )
}

/** 消息列表：基于 Spark Chat 的 Bubble 组件 */
ChatView.Messages = function ChatViewMessages() {
  const { messages, isLoading } = useChatView()
  return (
    <div className="chat-view__messages" role="log" aria-live="polite">
      {messages.map(msg => (
        <MessageBubble key={msg.id} message={msg} />
      ))}
      {isLoading && <TypingIndicator />}
    </div>
  )
}

/** 输入区域：基于 Spark Chat 的 Sender 组件 */
ChatView.Input = function ChatViewInput() {
  const { onSend, canSend } = useChatView()
  return (
    <div className="chat-view__input">
      <Sender
        placeholder="输入消息... (@选择 Agent)"
        onSubmit={onSend}
        disabled={!canSend}
      />
    </div>
  )
}
```

**使用方式：**

```tsx
<ChatView>
  <ChatView.Header />
  <ChatView.Messages />
  <ChatView.Input />
</ChatView>
```

#### AgentEditor（Agent 编辑器）

```tsx
/**
 * AgentEditor — Agent 创建/编辑面板
 * Compound Component 模式，各 Tab 独立可组合
 */
function AgentEditor({ agent, onSave }: AgentEditorProps) {
  return (
    <AgentEditorContext.Provider value={useAgentEditorState(agent)}>
      <div className="agent-editor">
        <AgentEditor.Identity />   {/* 名称、头像、描述、系统提示词 */}
        <AgentEditor.ModelConfig /> {/* 模型选择 + Fallback 链 */}
        <AgentEditor.Tools />       {/* 默认工具 + MCP + Skills */}
        <AgentEditor.Guardrails />  {/* 安全边界 */}
        <AgentEditor.Collaboration /> {/* 子 Agent 配置 */}
        <AgentEditor.Memory />      {/* 记忆策略 */}
        <AgentEditor.Actions onSave={onSave} />
      </div>
    </AgentEditorContext.Provider>
  )
}
```

#### MemoryPanel（三层记忆面板）

```tsx
/**
 * MemoryPanel — 三层记忆可视化面板
 * Tab 切换：Agent 记忆 / 用户记忆 / 会话记忆
 */
function MemoryPanel() {
  return (
    <MemoryPanelContext.Provider value={useMemoryState()}>
      <div className="memory-panel">
        <MemoryPanel.Tabs />     {/* Tab 切换器 */}
        <MemoryPanel.Search />   {/* 搜索/筛选 */}
        <MemoryPanel.List />     {/* 记忆条目列表 */}
      </div>
    </MemoryPanelContext.Provider>
  )
}

/** 记忆条目 — 独立可复用 */
function MemoryItem({ memory, onEdit, onDelete, onPin }: MemoryItemProps) {
  return (
    <div className={cn(
      "flex items-start gap-3 p-3 rounded-lg border border-border",
      "hover:bg-bg-secondary transition-colors",
      memory.pinned && "border-accent bg-accent/5"
    )}>
      <Avatar size="sm" src={memory.sourceAvatar} />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-text-primary line-clamp-2">
          {memory.content}
        </p>
        <div className="flex items-center gap-2 mt-1">
          <span className="text-xs text-text-secondary">
            {formatRelativeTime(memory.createdAt)}
          </span>
          {memory.pinned && <Badge variant="accent">已固定</Badge>}
        </div>
      </div>
      <DropdownMenu>
        <DropdownMenu.Item onClick={onPin}>
          {memory.pinned ? '取消固定' : '固定'}
        </DropdownMenu.Item>
        <DropdownMenu.Item onClick={onEdit}>编辑</DropdownMenu.Item>
        <DropdownMenu.Item onClick={onDelete} variant="danger">
          删除
        </DropdownMenu.Item>
      </DropdownMenu>
    </div>
  )
}
```

---

## 3. 主题系统（Multi-Theme + Dark/Light）

### 3.1 CSS Variables Token 体系

```css
/* tokens.css — 设计 Token 定义 */

:root {
  /* === Spacing Scale (4px base) === */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-6: 24px;
  --space-8: 32px;
  --space-12: 48px;
  --space-16: 64px;

  /* === Typography Scale === */
  --text-xs: 12px;
  --text-sm: 14px;
  --text-base: 16px;
  --text-lg: 18px;
  --text-xl: 20px;
  --text-2xl: 24px;
  --text-3xl: 30px;

  /* === Border Radius === */
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-full: 9999px;

  /* === Shadows === */
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.1);
  --shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.15);

  /* === Motion === */
  --duration-fast: 150ms;
  --duration-normal: 200ms;
  --duration-slow: 300ms;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in: cubic-bezier(0.7, 0, 0.84, 0);
}
```

### 3.2 Light Theme

```css
/* theme-light.css */
[data-theme="light"] {
  --color-bg-primary: #ffffff;
  --color-bg-secondary: #f9fafb;
  --color-bg-tertiary: #f3f4f6;
  --color-bg-hover: #f3f4f6;
  --color-bg-active: #e5e7eb;

  --color-text-primary: #111827;
  --color-text-secondary: #6b7280;
  --color-text-tertiary: #9ca3af;
  --color-text-inverse: #ffffff;

  --color-border: #e5e7eb;
  --color-border-strong: #d1d5db;

  --color-accent: #3b82f6;       /* Blue-500 */
  --color-accent-hover: #2563eb; /* Blue-600 */
  --color-accent-bg: #eff6ff;    /* Blue-50 */

  --color-success: #10b981;
  --color-warning: #f59e0b;
  --color-error: #ef4444;
  --color-info: #3b82f6;

  /* Agent 类型色板 */
  --color-agent-leader: #8b5cf6;  /* Purple */
  --color-agent-worker: #06b6d4;  /* Cyan */
  --color-agent-specialist: #f97316; /* Orange */
}
```

### 3.3 Dark Theme

```css
/* theme-dark.css */
[data-theme="dark"] {
  --color-bg-primary: #0f172a;     /* Slate-900 */
  --color-bg-secondary: #1e293b;   /* Slate-800 */
  --color-bg-tertiary: #334155;    /* Slate-700 */
  --color-bg-hover: #334155;
  --color-bg-active: #475569;

  --color-text-primary: #f8fafc;   /* Slate-50 */
  --color-text-secondary: #94a3b8; /* Slate-400 */
  --color-text-tertiary: #64748b;  /* Slate-500 */
  --color-text-inverse: #0f172a;

  --color-border: #334155;
  --color-border-strong: #475569;

  --color-accent: #60a5fa;         /* Blue-400 */
  --color-accent-hover: #93c5fd;   /* Blue-300 */
  --color-accent-bg: #1e3a5f;

  --color-success: #34d399;
  --color-warning: #fbbf24;
  --color-error: #f87171;
  --color-info: #60a5fa;

  --color-agent-leader: #a78bfa;
  --color-agent-worker: #22d3ee;
  --color-agent-specialist: #fb923c;

  /* Dark 模式 Shadow 需要更暗 */
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.4);
  --shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.5);
}
```

### 3.4 多主题扩展（未来）

```css
/* theme-ocean.css — 海洋主题示例 */
[data-theme="ocean"] {
  --color-accent: #0ea5e9;
  --color-bg-primary: #0c1222;
  --color-bg-secondary: #162032;
  /* ... */
}

/* theme-forest.css — 森林主题示例 */
[data-theme="forest"] {
  --color-accent: #22c55e;
  --color-bg-primary: #0a1f0a;
  /* ... */
}
```

### 3.5 Theme Provider（React 实现）

```tsx
/**
 * ThemeProvider — 主题上下文提供者
 * 管理主题切换、持久化、系统偏好检测
 */
function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    // 从 localStorage 恢复，或跟随系统
    return loadSavedTheme() ?? getSystemTheme()
  })

  // 应用主题到 DOM
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  const value: ThemeContextValue = useMemo(() => ({
    theme,
    setTheme,
    toggle: () => setTheme(t => t === 'light' ? 'dark' : 'light'),
    isDark: theme === 'dark' || theme === 'ocean' || theme === 'forest',
  }), [theme])

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  )
}
```

---

## 4. 性能策略（React Best Practices）

### 4.1 避免瀑布（async-parallel）

```tsx
/**
 * 在 ChatView 挂载时，并行加载所有需要的数据
 * 而不是串行 await
 */
function useChatInit(sessionId: string) {
  // 正确：Promise.all 并行加载
  const { data } = useSWR(sessionId, async () => {
    const [messages, agents, session] = await Promise.all([
      api.getMessages(sessionId),
      api.getAgents(),
      api.getSession(sessionId),
    ])
    return { messages, agents, session }
  })
  return data
}
```

### 4.2 Bundle Size（bundle-dynamic-imports）

```tsx
/**
 * 重型组件动态导入 — 只在需要时加载
 * 遵循 bundle-dynamic-imports 规则
 */
const AgentRelationGraph = lazy(() =>
  import('./components/agent/AgentRelationGraph')
)

const MCPBrowser = lazy(() =>
  import('./components/tools/MCPBrowser')
)

const ObservabilityPanel = lazy(() =>
  import('./components/observability/ObservabilityPanel')
)

// 使用 Suspense 包裹
function MainContent({ view }: { view: ViewType }) {
  return (
    <Suspense fallback={<PageSkeleton />}>
      {view === 'chat' && <ChatPage />}
      {view === 'agents' && <AgentTeamPage />}
      {view === 'mcp' && <MCPBrowser />}
      {view === 'skills' && <SkillsBrowser />}
    </Suspense>
  )
}
```

### 4.3 Re-render 优化（rerender-derived-state-no-effect）

```tsx
/**
 * 正确：在渲染中计算派生状态，不用 useEffect
 * 遵循 rerender-derived-state-no-effect 规则
 */
function AgentList({ agents, searchQuery }: AgentListProps) {
  // 直接计算，不需要 useState + useEffect
  const filteredAgents = useMemo(
    () => agents.filter(a =>
      a.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      a.description.toLowerCase().includes(searchQuery.toLowerCase())
    ),
    [agents, searchQuery]
  )

  return (
    <ul role="list">
      {filteredAgents.map(agent => (
        <AgentCard key={agent.id} agent={agent} />
      ))}
    </ul>
  )
}
```

### 4.4 消息列表虚拟化（rendering-content-visibility）

```tsx
/**
 * 消息列表 — 对长列表使用 content-visibility 优化
 * 遵循 rendering-content-visibility 规则
 */
function MessageList({ messages }: { messages: Message[] }) {
  return (
    <div className="chat-view__messages" role="log" aria-live="polite">
      {messages.map(msg => (
        <div
          key={msg.id}
          style={{ contentVisibility: 'auto', containIntrinsicSize: '0 80px' }}
        >
          <MessageBubble message={msg} />
        </div>
      ))}
    </div>
  )
}
```

### 4.5 Event Stream 连接（advanced-init-once）

```tsx
/**
 * Event Stream 连接 — 全局唯一，应用生命周期内只初始化一次
 * 遵循 advanced-init-once 规则
 */
const eventStream = new EventStreamService()

function EventStreamProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    // 只在 mount 时连接一次
    eventStream.connect()
    eventStream.on('connected', () => setConnected(true))
    eventStream.on('disconnected', () => setConnected(false))

    return () => eventStream.disconnect()
  }, [])

  return (
    <EventStreamContext.Provider value={{ connected, stream: eventStream }}>
      {children}
    </EventStreamContext.Provider>
  )
}
```

---

## 5. Event Stream 实时数据流

### 5.1 Event Stream Hook

```tsx
/**
 * useEventStream — 订阅 AgentScope 事件流
 * 处理 TEXT_BLOCK_DELTA / TOOL_CALL_START / REPLY_END 等事件
 */
function useEventStream(sessionId: string) {
  const { stream } = useEventStreamContext()
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [isStreaming, setIsStreaming] = useState(false)

  useEffect(() => {
    const unsubscribe = stream.subscribe(sessionId, (event) => {
      switch (event.type) {
        case 'reply_start':
          setIsStreaming(true)
          break
        case 'text_block_delta':
          // 增量追加文本内容
          setEvents(prev => appendDelta(prev, event))
          break
        case 'tool_call_start':
          // 展示工具调用 Block
          setEvents(prev => [...prev, event])
          break
        case 'reply_end':
          setIsStreaming(false)
          break
      }
    })

    return unsubscribe
  }, [sessionId, stream])

  return { events, isStreaming }
}
```

### 5.2 工具调用 Block 组件

```tsx
/**
 * ToolCallBlock — 展示 Agent 工具调用的输入/输出
 * 可展开/折叠查看详情
 */
function ToolCallBlock({ event }: { event: ToolCallEvent }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className={cn(
      "rounded-lg border border-border overflow-hidden",
      "text-sm transition-all",
      event.status === 'running' && "border-accent"
    )}>
      {/* 头部：工具名 + 状态 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className={cn(
          "flex items-center gap-2 w-full px-3 py-2",
          "hover:bg-bg-hover transition-colors",
          "text-start"
        )}
        aria-expanded={expanded}
      >
        <ToolIcon name={event.tool} />
        <span className="font-medium text-text-primary">
          {event.tool}
        </span>
        <StatusBadge status={event.status} />
        {event.durationMs && (
          <span className="text-text-tertiary ml-auto">
            {event.durationMs}ms
          </span>
        )}
        <ChevronIcon
          className={cn("ml-auto transition-transform", expanded && "rotate-180")}
        />
      </button>

      {/* 展开：输入/输出详情 */}
      {expanded && (
        <div className="border-t border-border">
          <CodeBlock label="Input" code={JSON.stringify(event.input, null, 2)} />
          {event.output && (
            <CodeBlock label="Output" code={event.output} />
          )}
        </div>
      )}
    </div>
  )
}
```

---

## 6. 可访问性检查清单

| 检查项 | 实现方式 | 状态 |
|--------|---------|------|
| 键盘导航 | 所有交互元素可 Tab 访问，Activity Bar 用 arrow keys | ✅ |
| Focus 状态 | 所有按钮/链接有 visible focus ring | ✅ |
| ARIA 标签 | 图标按钮有 aria-label，消息列表有 role="log" | ✅ |
| 语义 HTML | nav/main/aside/header/footer/section | ✅ |
| 对比度 | WCAG AA (4.5:1 文本, 3:1 大文本) | ✅ |
| 动画偏好 | prefers-reduced-motion 媒体查询 | ✅ |
| 屏幕阅读器 | 图标按钮有 sr-only 文本 | ✅ |
| 无色彩依赖 | 状态用图标+颜色双通道表达 | ✅ |

---

## 7. 文件结构（前端）

```
frontend/
├── package.json
├── tsconfig.json
├── tailwind.config.ts          # Tailwind 配置（如果使用 Tailwind 辅助）
├── vite.config.ts              # Vite 构建配置
├── index.html
│
├── public/
│   ├── favicon.svg
│   └── icons/                  # Agent 默认图标
│
└── src/
    ├── main.tsx                # 应用入口
    ├── App.tsx                 # 根组件 (Provider 组合)
    │
    ├── layouts/                # 布局组件
    │   ├── AppLayout.tsx       # 全局 Grid 布局
    │   ├── ActivityBar.tsx     # 左侧图标导航
    │   ├── StatusBar.tsx       # 底部状态栏
    │   └── TitleBar.tsx        # Electron 标题栏
    │
    ├── pages/                  # 页面级组件
    │   ├── ChatPage.tsx        # Chat 页面
    │   ├── AgentTeamPage.tsx   # Agent Team 管理
    │   ├── MCPPage.tsx         # MCP 浏览器
    │   ├── SkillsPage.tsx      # Skills 浏览器
    │   └── SettingsPage.tsx    # 设置页面
    │
    ├── features/               # 功能模块
    │   ├── chat/
    │   │   ├── ChatView.tsx         # 对话视图 (Compound)
    │   │   ├── MessageBubble.tsx    # 消息气泡 (复用 Spark Chat)
    │   │   ├── AgentSelector.tsx    # Agent 选择器
    │   │   ├── ToolCallBlock.tsx    # 工具调用展示
    │   │   ├── ThinkingChain.tsx    # 思考链展示
    │   │   └── HumanApprovalDialog.tsx # 人工审批弹窗
    │   │
    │   ├── agents/
    │   │   ├── AgentList.tsx        # Agent 列表
    │   │   ├── AgentCard.tsx        # Agent 卡片
    │   │   ├── AgentEditor.tsx      # Agent 编辑器 (Compound)
    │   │   ├── AgentTeamView.tsx    # Team 关系图
    │   │   ├── ModelConfig.tsx      # 模型配置 (主+降级链)
    │   │   ├── ToolSelector.tsx     # 工具选择器
    │   │   └── GuardrailsForm.tsx   # 安全边界表单
    │   │
    │   ├── memory/
    │   │   ├── MemoryPanel.tsx      # 三层记忆面板 (Compound)
    │   │   ├── MemoryList.tsx       # 记忆条目列表
    │   │   ├── MemoryItem.tsx       # 单条记忆
    │   │   └── MemoryEditor.tsx     # 记忆编辑弹窗
    │   │
    │   ├── tools/
    │   │   ├── MCPBrowser.tsx       # MCP 服务器浏览
    │   │   ├── MCPCard.tsx          # MCP 服务器卡片
    │   │   ├── SkillsBrowser.tsx    # Skills 浏览
    │   │   └── SkillCard.tsx        # Skill 卡片
    │   │
    │   └── observability/
    │       ├── ObservabilityPanel.tsx # 可观测面板
    │       ├── TokenChart.tsx         # Token 用量图表
    │       └── CallLogTable.tsx       # 调用日志表格
    │
    ├── hooks/                  # 自定义 Hooks
    │   ├── useAgent.ts         # Agent CRUD
    │   ├── useChat.ts          # 对话管理
    │   ├── useEventStream.ts   # 事件流订阅
    │   ├── useMemory.ts        # 记忆管理
    │   ├── useTheme.ts         # 主题切换
    │   └── useBackend.ts       # 后端 API 调用
    │
    ├── services/               # API 服务层
    │   ├── api.ts              # HTTP API 封装
    │   ├── eventStream.ts      # SSE Event Stream 客户端
    │   └── types.ts            # API 响应类型
    │
    ├── stores/                 # 状态管理 (Zustand)
    │   ├── agentStore.ts       # Agent 状态
    │   ├── chatStore.ts        # 对话状态
    │   ├── settingsStore.ts    # 设置状态
    │   └── useAppStore.ts      # 全局状态
    │
    ├── themes/                 # 主题定义
    │   ├── tokens.css          # 设计 Token
    │   ├── theme-light.css     # 亮色主题
    │   ├── theme-dark.css      # 暗色主题
    │   └── index.css           # 主题入口
    │
    ├── components/             # 通用 UI 组件
    │   ├── Badge.tsx
    │   ├── StatusDot.tsx
    │   ├── CodeBlock.tsx
    │   ├── PageSkeleton.tsx    # 页面加载骨架屏
    │   └── EmptyState.tsx      # 空状态占位
    │
    ├── utils/                  # 工具函数
    │   ├── cn.ts               # clsx + twMerge 封装
    │   ├── formatTime.ts       # 时间格式化
    │   └── eventParser.ts      # 事件解析
    │
    └── types/                  # TypeScript 类型
        ├── agent.ts
        ├── chat.ts
        ├── memory.ts
        ├── mcp.ts
        └── theme.ts
```

---

## 8. 关键交互流程

### 8.1 Chat 对话流程

```
用户输入消息
  │
  ├─→ @选择 Agent？
  │     └─→ 弹出 Agent 选择器（Dropdown）
  │
  ├─→ 发送消息 → POST /api/chat/stream
  │     │
  │     └─→ SSE Event Stream
  │           │
  │           ├─ reply_start → 显示 Agent 头像 + "正在思考"
  │           ├─ text_block_delta → 流式打字效果
  │           ├─ tool_call_start → 插入 ToolCallBlock
  │           │     └─ 需要人工审批？→ 弹出 HumanApprovalDialog
  │           ├─ tool_call_end → 更新 ToolCallBlock 结果
  │           ├─ model_fallback → 显示通知条 "已切换到 qwen-plus"
  │           └─ reply_end → 完成，显示 Token 用量
  │
  └─→ Agent 调用子 Agent？
        └─→ 显示嵌套的 Agent 回复（缩进 + 不同头像色）
```

### 8.2 Agent 创建流程

```
用户点击 "创建 Agent"
  │
  └─→ AgentEditor 表单
        │
        ├─ 1. 身份信息
        │     ├─ 名称 + 头像上传
        │     ├─ 描述（一句话）
        │     └─ 系统提示词（多行编辑器）
        │
        ├─ 2. 模型配置
        │     ├─ 主模型选择（下拉: qwen-max/plus/turbo）
        │     ├─ Fallback 链（可排序的模型列表）
        │     └─ 参数（temperature / max_tokens）
        │
        ├─ 3. 工具配置
        │     ├─ 默认工具（锁定，展示但不可取消）
        │     ├─ MCP 工具（从 MCP 浏览器选择）
        │     └─ Skills（从 Skills 浏览器选择）
        │
        ├─ 4. 安全边界
        │     ├─ 文件系统范围
        │     ├─ Shell 黑白名单
        │     └─ 人工审批规则
        │
        ├─ 5. 协作配置
        │     ├─ 可调用子 Agent（多选）
        │     └─ 最大调用深度
        │
        └─ 6. 保存 → POST /api/agents
```

---

## 9. 与 Spark Design / Spark Chat 的集成

### 9.1 直接使用 Spark Design 组件

| 用途 | Spark Design 组件 |
|------|-------------------|
| 按钮 | `Button` |
| 输入框 | `Input`, `TextArea` |
| 下拉选择 | `Select` |
| 弹窗 | `Modal` |
| 通知 | `Notification`, `Message` |
| 标签页 | `Tabs` |
| 卡片 | `Card` |
| 标签 | `Tag` |
| 开关 | `Switch` |
| 滑动条 | `Slider` |
| 表格 | `Table` |
| 树形控件 | `Tree`（Agent Team 关系） |
| 抽屉 | `Drawer`（Detail Panel） |

### 9.2 直接使用 Spark Chat 组件

| 用途 | Spark Chat 组件 |
|------|----------------|
| 对话容器 | `ChatAnywhere` |
| 消息气泡 | `Bubble` |
| 输入框 | `Sender` |
| Markdown 渲染 | `Markdown` |
| 图表渲染 | `Mermaid` |
| 会话列表 | `Conversations` |

### 9.3 自定义扩展层

在 Spark Design 基础上封装的业务组件：

```tsx
/**
 * AgentBadge — 在 Spark Design Tag 基础上定制
 * 展示 Agent 头像 + 名称 + 状态
 */
function AgentBadge({ agent }: { agent: Agent }) {
  return (
    <div className="flex items-center gap-2 px-2 py-1 rounded-full bg-bg-secondary">
      <Avatar size="xs" src={agent.avatar} />
      <span className="text-sm font-medium">{agent.name}</span>
      <StatusDot status={agent.status} />
    </div>
  )
}
```

---

## 10. 设计 Token 速查表

| Token | Light | Dark | 用途 |
|-------|-------|------|------|
| `--color-bg-primary` | #ffffff | #0f172a | 主背景 |
| `--color-bg-secondary` | #f9fafb | #1e293b | 侧边栏/卡片 |
| `--color-bg-hover` | #f3f4f6 | #334155 | 悬停态 |
| `--color-text-primary` | #111827 | #f8fafc | 主文字 |
| `--color-text-secondary` | #6b7280 | #94a3b8 | 次要文字 |
| `--color-border` | #e5e7eb | #334155 | 边框 |
| `--color-accent` | #3b82f6 | #60a5fa | 主题色 |
| `--space-4` | 16px | 16px | 组件内边距 |
| `--space-6` | 24px | 24px | 区块间距 |
| `--radius-md` | 8px | 8px | 卡片圆角 |

---

## 11. 下一步

1. **P0**：初始化项目 → Vite + React + TS → 安装 Spark Design/Chat → 搭建 AppLayout 骨架
2. **P1**：实现 ChatPage（对接 AgentScope Event Stream）
3. **P2**：实现 AgentTeamPage（Agent CRUD + 工具选择）
4. **P3**：实现 MemoryPanel / MCPBrowser / SkillsBrowser
5. **P4**：主题系统 + 可观测性面板
