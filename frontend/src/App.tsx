/**
 * App — 应用根组件
 *
 * 职责：
 * 1. 应用主题（通过 data-theme 属性同步到 DOM）
 * 2. 缓存已访问页面并根据当前视图显示对应页面
 * 3. 包裹 AppLayout 提供全局布局
 *
 * 设计原则（vercel-react-best-practices）：
 * - rerender-derived-state-no-effect: 主题应用到 DOM 用 useLayoutEffect
 * - advanced-init-once: 初始化检查只做一次
 */

import { lazy, Suspense, useEffect, useLayoutEffect, useState, type ComponentType } from 'react';
import { App as AntdApp } from 'antd';
import { StyleProvider } from '@ant-design/cssinjs';
import { ConfigProvider, Spinner } from '@/components/design';
import { carbonDarkTheme, carbonTheme } from '@/components/design/theme';
import { useAppStore } from './stores/appStore';
import type { ViewType } from './stores/appStore';
import { useSettingsStore } from './stores/settingsStore';
import { setDesktopApiOrigin } from './utils/apiUrl';
import { AppLayout } from './layouts/AppLayout';
import { FeedbackBridge } from './components/FeedbackBridge';
import { DesktopBackendGate } from './components/DesktopBackendGate';
import { ApiKeyOnboardingModal } from './features/settings/ApiKeyOnboardingModal';
import type { DesktopBackendStatus } from './types/desktopBackend';

const ChatPage = lazy(() => import('./pages/ChatPage').then((module) => ({ default: module.ChatPage })));
const WorkflowPage = lazy(() => import('./pages/WorkflowPage').then((module) => ({ default: module.WorkflowPage })));
const RunsPage = lazy(() => import('./pages/RunsPage').then((module) => ({ default: module.RunsPage })));
const TracePage = lazy(() => import('./pages/TracePage').then((module) => ({ default: module.TracePage })));
const SandboxPage = lazy(() => import('./pages/SandboxPage').then((module) => ({ default: module.SandboxPage })));
const AgentsPage = lazy(() => import('./pages/AgentsPage').then((module) => ({ default: module.AgentsPage })));
const SubagentsPage = lazy(() => import('./pages/SubagentsPage').then((module) => ({ default: module.SubagentsPage })));
const ToolsPage = lazy(() => import('./pages/ToolsPage').then((module) => ({ default: module.ToolsPage })));
const MCPPage = lazy(() => import('./pages/MCPPage').then((module) => ({ default: module.MCPPage })));
const SkillsPage = lazy(() => import('./pages/SkillsPage').then((module) => ({ default: module.SkillsPage })));
const MemoryPage = lazy(() => import('./pages/MemoryPage').then((module) => ({ default: module.MemoryPage })));
const ScheduledJobsPage = lazy(() => import('./pages/ScheduledJobsPage').then((module) => ({ default: module.ScheduledJobsPage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })));

/**
 * 视图 → 页面组件映射表
 * 使用对象映射代替 switch/if，遵循 js-index-maps 规则
 */
const VIEW_PAGES: Record<ViewType, ComponentType> = {
  chat: ChatPage,
  runs: RunsPage,
  jobs: ScheduledJobsPage,
  traces: TracePage,
  sandbox: SandboxPage,
  agents: AgentsPage,
  subagents: SubagentsPage,
  tools: ToolsPage,
  mcp: MCPPage,
  skills: SkillsPage,
  memory: MemoryPage,
  settings: SettingsPage,
};

const KEEP_ALIVE_VIEWS = new Set<ViewType>(['chat']);
const CACHEABLE_VIEWS = new Set<ViewType>(['chat', 'agents', 'sandbox']);
const MAX_CACHED_VIEWS = 3;

/**
 * App 根组件
 */
export function App() {
  const currentView = useAppStore((s) => s.currentView);
  const workspaceMode = useAppStore((s) => s.workspaceMode);
  const setBackendConnected = useAppStore((s) => s.setBackendConnected);
  const theme = useSettingsStore((s) => s.theme);
  const backendUrl = useSettingsStore((s) => s.backendUrl);
  const setBackendUrl = useSettingsStore((s) => s.setBackendUrl);
  const setAppVersion = useSettingsStore((s) => s.setAppVersion);
  const [cachedViews, setCachedViews] = useState<ViewType[]>(['chat']);
  const isDesktopShell = Boolean(window.electronAPI);
  const [desktopBackendStatus, setDesktopBackendStatus] = useState<DesktopBackendStatus>(
    isDesktopShell ? { state: 'starting' } : { state: 'ready' },
  );

  // 同步主题到 DOM（useLayoutEffect 确保在绘制前应用，避免闪烁）
  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  useEffect(() => {
    const electronAPI = window.electronAPI;
    if (!electronAPI) {
      return;
    }

    let mounted = true;

    const loadDesktopRuntimeInfo = async () => {
      const [runtimeBackendUrl, runtimeVersion] = await Promise.all([
        electronAPI.getBackendUrl(),
        electronAPI.getAppVersion(),
      ]);

      if (!mounted) {
        return;
      }

      setBackendUrl(runtimeBackendUrl);
      setDesktopApiOrigin(runtimeBackendUrl);
      setAppVersion(runtimeVersion);
    };

    void loadDesktopRuntimeInfo();
    return () => {
      mounted = false;
    };
  }, [setAppVersion, setBackendUrl]);

  useEffect(() => {
    const electronAPI = window.electronAPI;
    if (!electronAPI) {
      return;
    }

    const unsubscribe = electronAPI.onBackendStatus(setDesktopBackendStatus);
    void electronAPI.getBackendStatus().then(setDesktopBackendStatus);
    return unsubscribe;
  }, []);

  useEffect(() => {
    let mounted = true;

    const checkBackend = async () => {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 3000);
      const healthUrl = window.electronAPI ? `${backendUrl}/api/health` : '/api/health';
      try {
        const response = await fetch(healthUrl, {
          signal: controller.signal,
          cache: 'no-store',
        });
        if (mounted) {
          setBackendConnected(response.ok);
        }
      } catch {
        if (mounted) {
          setBackendConnected(false);
        }
      } finally {
        window.clearTimeout(timeout);
      }
    };

    void checkBackend();
    const interval = window.setInterval(checkBackend, 10000);
    return () => {
      mounted = false;
      window.clearInterval(interval);
    };
  }, [backendUrl, setBackendConnected]);

  useEffect(() => {
    setCachedViews((views) => nextCachedViews(views, currentView));
  }, [currentView]);

  const sparkTheme = theme === 'dark' ? carbonDarkTheme : carbonTheme;
  const retryDesktopBackend = () => {
    void window.electronAPI?.restartBackend();
  };

  return (
    <StyleProvider linters={[]}>
      <ConfigProvider {...sparkTheme}>
        <AntdApp>
          {isDesktopShell && desktopBackendStatus.state !== 'ready' ? (
            <DesktopBackendGate status={desktopBackendStatus} onRetry={retryDesktopBackend} />
          ) : (
            <>
              <FeedbackBridge />
              <ApiKeyOnboardingModal />
              <AppLayout>
                <WorkspacePages
                  workspaceMode={workspaceMode}
                  activeView={currentView}
                  cachedViews={cachedViews}
                />
              </AppLayout>
            </>
          )}
        </AntdApp>
      </ConfigProvider>
    </StyleProvider>
  );
}

interface WorkspacePagesProps {
  workspaceMode: 'workstation' | 'workflow';
  activeView: ViewType;
  cachedViews: ViewType[];
}

function WorkspacePages({ workspaceMode, activeView, cachedViews }: WorkspacePagesProps) {
  const workflowActive = workspaceMode === 'workflow';
  return (
    <div className="app-workspace-switcher" data-mode={workspaceMode}>
      <section
        className="app-workspace-pane app-workspace-pane--workstation"
        aria-hidden={workflowActive}
      >
        <CachedPages activeView={activeView} cachedViews={cachedViews} />
      </section>
      <section
        className="app-workspace-pane app-workspace-pane--workflow"
        aria-hidden={!workflowActive}
      >
        <Suspense fallback={<PageSkeleton />}>
          <WorkflowPage />
        </Suspense>
      </section>
    </div>
  );
}

function nextCachedViews(current: ViewType[], activeView: ViewType): ViewType[] {
  const keepAliveViews = Array.from(KEEP_ALIVE_VIEWS);
  if (!CACHEABLE_VIEWS.has(activeView)) {
    return KEEP_ALIVE_VIEWS.has(activeView)
      ? keepAliveViews
      : [...keepAliveViews, activeView];
  }

  if (KEEP_ALIVE_VIEWS.has(activeView)) {
    return keepAliveViews;
  }

  const recentViews = current
    .filter((view) => view !== activeView && !KEEP_ALIVE_VIEWS.has(view) && CACHEABLE_VIEWS.has(view));
  const next = [...keepAliveViews, ...recentViews, activeView];
  const keepAliveCount = keepAliveViews.length;
  const overflow = Math.max(0, next.length - MAX_CACHED_VIEWS);
  if (overflow === 0) {
    return next;
  }

  return [
    ...next.slice(0, keepAliveCount),
    ...next.slice(keepAliveCount + overflow),
  ];
}

interface CachedPagesProps {
  activeView: ViewType;
  cachedViews: ViewType[];
}

function CachedPages({ activeView, cachedViews }: CachedPagesProps) {
  const uniqueCachedViews = Array.from(new Set(cachedViews));
  return (
    <div className="h-full min-h-0 overflow-hidden">
      {uniqueCachedViews.map((view) => {
        const PageComponent = VIEW_PAGES[view] ?? ChatPage;
        const active = view === activeView;
        return (
          <section
            key={view}
            className="h-full min-h-0 overflow-hidden"
            style={{ display: active ? 'block' : 'none' }}
            aria-hidden={!active}
          >
            <Suspense fallback={<PageSkeleton />}>
              <PageComponent />
            </Suspense>
          </section>
        );
      })}
    </div>
  );
}

function PageSkeleton() {
  return (
    <div className="flex h-full min-h-0 items-center justify-center">
      <div className="flex items-center gap-2 rounded-lg bg-[var(--color-bg-secondary)] px-4 py-3 text-sm text-[var(--color-text-secondary)] shadow-sm">
        <Spinner />
        页面加载中
      </div>
    </div>
  );
}
