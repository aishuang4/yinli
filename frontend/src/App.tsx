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
import { ConfigProvider, Spinner, carbonDarkTheme, carbonTheme } from '@agentscope-ai/design';
import { useAppStore } from './stores/appStore';
import type { ViewType } from './stores/appStore';
import { useSettingsStore } from './stores/settingsStore';
import { AppLayout } from './layouts/AppLayout';

const ChatPage = lazy(() => import('./pages/ChatPage').then((module) => ({ default: module.ChatPage })));
const TracePage = lazy(() => import('./pages/TracePage').then((module) => ({ default: module.TracePage })));
const SandboxPage = lazy(() => import('./pages/SandboxPage').then((module) => ({ default: module.SandboxPage })));
const AgentTeamPage = lazy(() => import('./pages/AgentTeamPage').then((module) => ({ default: module.AgentTeamPage })));
const MCPPage = lazy(() => import('./pages/MCPPage').then((module) => ({ default: module.MCPPage })));
const SkillsPage = lazy(() => import('./pages/SkillsPage').then((module) => ({ default: module.SkillsPage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })));

/**
 * 视图 → 页面组件映射表
 * 使用对象映射代替 switch/if，遵循 js-index-maps 规则
 */
const VIEW_PAGES: Record<ViewType, ComponentType> = {
  chat: ChatPage,
  traces: TracePage,
  sandbox: SandboxPage,
  agents: AgentTeamPage,
  mcp: MCPPage,
  skills: SkillsPage,
  settings: SettingsPage,
};

/**
 * App 根组件
 */
export function App() {
  const currentView = useAppStore((s) => s.currentView);
  const setBackendConnected = useAppStore((s) => s.setBackendConnected);
  const theme = useSettingsStore((s) => s.theme);
  const backendUrl = useSettingsStore((s) => s.backendUrl);
  const setBackendUrl = useSettingsStore((s) => s.setBackendUrl);
  const setAppVersion = useSettingsStore((s) => s.setAppVersion);
  const [cachedViews, setCachedViews] = useState<ViewType[]>(['chat']);

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
      setAppVersion(runtimeVersion);
    };

    void loadDesktopRuntimeInfo();
    return () => {
      mounted = false;
    };
  }, [setAppVersion, setBackendUrl]);

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
    setCachedViews((views) => (
      views.includes(currentView) ? views : [...views, currentView]
    ));
  }, [currentView]);

  const sparkTheme = theme === 'dark' ? carbonDarkTheme : carbonTheme;

  return (
    <ConfigProvider {...sparkTheme}>
      <AppLayout>
        <CachedPages activeView={currentView} cachedViews={cachedViews} />
      </AppLayout>
    </ConfigProvider>
  );
}

interface CachedPagesProps {
  activeView: ViewType;
  cachedViews: ViewType[];
}

function CachedPages({ activeView, cachedViews }: CachedPagesProps) {
  return (
    <div className="h-full min-h-0">
      {cachedViews.map((view) => {
        const PageComponent = VIEW_PAGES[view] ?? ChatPage;
        const active = view === activeView;
        return (
          <section
            key={view}
            className="h-full min-h-0"
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
