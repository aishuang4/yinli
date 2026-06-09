/**
 * App — 应用根组件
 *
 * 职责：
 * 1. 应用主题（通过 data-theme 属性同步到 DOM）
 * 2. 根据当前视图渲染对应页面
 * 3. 包裹 AppLayout 提供全局布局
 *
 * 设计原则（vercel-react-best-practices）：
 * - rerender-derived-state-no-effect: 主题应用到 DOM 用 useLayoutEffect
 * - advanced-init-once: 初始化检查只做一次
 */

import { useEffect, useLayoutEffect } from 'react';
import { ConfigProvider, carbonDarkTheme, carbonTheme } from '@agentscope-ai/design';
import { useAppStore } from './stores/appStore';
import { useSettingsStore } from './stores/settingsStore';
import { AppLayout } from './layouts/AppLayout';
import { ChatPage } from './pages/ChatPage';
import { AgentTeamPage } from './pages/AgentTeamPage';
import { MCPPage } from './pages/MCPPage';
import { SkillsPage } from './pages/SkillsPage';
import { SettingsPage } from './pages/SettingsPage';

/**
 * 视图 → 页面组件映射表
 * 使用对象映射代替 switch/if，遵循 js-index-maps 规则
 */
const VIEW_PAGES: Record<string, React.ComponentType> = {
  chat: ChatPage,
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

  // 同步主题到 DOM（useLayoutEffect 确保在绘制前应用，避免闪烁）
  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  useEffect(() => {
    let mounted = true;

    const checkBackend = async () => {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 3000);
      try {
        const response = await fetch('/api/health', {
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
  }, [setBackendConnected]);

  // 根据当前视图获取页面组件
  const PageComponent = VIEW_PAGES[currentView] ?? ChatPage;
  const sparkTheme = theme === 'dark' ? carbonDarkTheme : carbonTheme;

  return (
    <ConfigProvider {...sparkTheme}>
      <AppLayout>
        <PageComponent />
      </AppLayout>
    </ConfigProvider>
  );
}
