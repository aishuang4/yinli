/**
 * AppLayout — 全局应用布局组件
 *
 * 使用 CSS Grid 实现多面板 IDE 风格布局。
 * 包含 TitleBar / ActivityBar / MainContent / DetailPanel / StatusBar。
 * DetailPanel 可通过 useAppStore 控制展开/收起。
 *
 * @see 前端设计文档 §1.1 整体布局架构
 */

import { type ComponentType, type ReactNode } from 'react';
import { Activity, ArrowLeft, Bot, Brain, CalendarClock, FlaskConical, GitBranch, ListRestart, Network, Package, Settings, Wrench } from 'lucide-react';
import { useAppStore, type ViewType, type WorkspaceMode } from '../stores/appStore';
import { TitleBar } from './TitleBar';
import { ActivityBar } from './ActivityBar';
import { StatusBar } from './StatusBar';
import './AppLayout.css';

interface AppLayoutProps {
  /** 主内容区域渲染的内容 */
  children: ReactNode;
  /** 详情面板渲染的内容（可选） */
  detail?: ReactNode;
}

interface WorkbenchMenuItem {
  id: ViewType | 'workflow';
  label: string;
  description: string;
  icon: ComponentType<{ size?: number; strokeWidth?: number }>;
  workspaceMode?: WorkspaceMode;
}

/** 工作台菜单按 ViewType 穷尽，chat 走 ActivityBar，workflow 是顶层模式。 */
const WORKBENCH_PAGES: Record<Exclude<ViewType, 'chat'>, WorkbenchMenuItem> = {
  agents: { id: 'agents', label: 'Agents', description: 'Agent 模型、工具与运行边界', icon: Bot },
  subagents: { id: 'subagents', label: 'Subagents', description: '子 Agent 定义与调用历史', icon: Network },
  tools: { id: 'tools', label: 'Tools', description: '内置工具与 HTTP 工具', icon: Wrench },
  mcp: { id: 'mcp', label: 'MCP', description: '外部工具服务连接', icon: GitBranch },
  skills: { id: 'skills', label: 'Skills', description: '技能管理与注入', icon: Package },
  memory: { id: 'memory', label: 'Memory', description: '记忆、审计与 Wiki', icon: Brain },
  traces: { id: 'traces', label: 'Trace', description: '运行轨迹与事件详情', icon: Activity },
  runs: { id: 'runs', label: 'Runs', description: '运行任务与恢复', icon: ListRestart },
  jobs: { id: 'jobs', label: '定时任务', description: '周期任务、确认启用与运行历史', icon: CalendarClock },
  sandbox: { id: 'sandbox', label: 'Sandbox', description: '测试沙盒', icon: FlaskConical },
  settings: { id: 'settings', label: 'Settings', description: '模型、主题与系统设置', icon: Settings },
};

const WORKBENCH_MENU_ITEMS: WorkbenchMenuItem[] = [
  WORKBENCH_PAGES.agents,
  WORKBENCH_PAGES.subagents,
  WORKBENCH_PAGES.tools,
  WORKBENCH_PAGES.mcp,
  WORKBENCH_PAGES.skills,
  WORKBENCH_PAGES.memory,
  WORKBENCH_PAGES.traces,
  WORKBENCH_PAGES.runs,
  WORKBENCH_PAGES.jobs,
  WORKBENCH_PAGES.sandbox,
  { id: 'workflow', label: 'Workflow', description: '动态任务流工作空间', icon: GitBranch, workspaceMode: 'workflow' },
  WORKBENCH_PAGES.settings,
];

/**
 * AppLayout 组件
 * 应用根布局，管理多面板的 Grid 结构
 */
export function AppLayout({ children, detail }: AppLayoutProps) {
  const detailPanelOpen = useAppStore((s) => s.detailPanelOpen);
  const workspaceMode = useAppStore((s) => s.workspaceMode);
  const currentView = useAppStore((s) => s.currentView);
  const electronShell = Boolean(window.electronAPI);
  const chatWorkbench = workspaceMode === 'workstation' && currentView === 'chat';
  const workbenchMenuPage = workspaceMode === 'workstation' && currentView !== 'chat';

  return (
    <div
      className="app-layout"
      data-detail-open={detailPanelOpen}
      data-workspace-mode={workspaceMode}
      data-electron-shell={electronShell}
      data-chat-workbench={chatWorkbench}
      data-workbench-menu-page={workbenchMenuPage}
    >
      {/* 自定义标题栏（Electron 无边框窗口） */}
      <div className="app-layout__titlebar">
        <TitleBar />
      </div>

      {/* 左侧活动导航栏 */}
      <div className="app-layout__activity-bar">
        {workbenchMenuPage ? <WorkbenchMenu /> : <ActivityBar />}
      </div>

      {/* 主内容区域 */}
      <main className="app-layout__main">
        {children}
      </main>

      {/* 右侧详情面板（可折叠） */}
      {detailPanelOpen && detail && (
        <aside className="app-layout__detail">
          {detail}
        </aside>
      )}

      {/* 底部状态栏 */}
      <div className="app-layout__statusbar">
        <StatusBar />
      </div>
    </div>
  );
}

function WorkbenchMenu() {
  const currentView = useAppStore((s) => s.currentView);
  const setView = useAppStore((s) => s.setView);
  const setWorkspaceMode = useAppStore((s) => s.setWorkspaceMode);

  return (
    <nav
      className="workbench-menu-page"
      data-electron-shell={Boolean(window.electronAPI)}
      style={window.electronAPI ? { paddingTop: 56 } : undefined}
      aria-label="工作台功能菜单"
    >
      <button
        type="button"
        className="workbench-menu-page__back"
        onClick={() => setView('chat')}
      >
        <ArrowLeft size={16} strokeWidth={1.8} />
        <span>返回 Chat</span>
      </button>
      <div className="workbench-menu-page__items">
        {WORKBENCH_MENU_ITEMS.map((item) => {
          const active = currentView === item.id;
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              className="workbench-menu-page__item"
              data-active={active}
              onClick={() => {
                if (item.workspaceMode) {
                  setWorkspaceMode(item.workspaceMode);
                  return;
                }
                setView(item.id as ViewType);
              }}
              aria-current={active ? 'page' : undefined}
            >
              <span className="workbench-menu-page__item-icon">
                <Icon size={17} strokeWidth={1.7} />
              </span>
              <span className="workbench-menu-page__item-copy">
                <span className="workbench-menu-page__item-label">{item.label}</span>
                <span className="workbench-menu-page__item-description">{item.description}</span>
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
