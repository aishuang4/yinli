/**
 * ActivityBar — 左侧图标导航栏
 *
 * 提供主要视图切换（Chat / Agents / MCP / Skills / Settings），
 * 底部固定主题切换按钮。
 *
 * 遵循 web-design-guidelines:
 * - 所有图标按钮有 aria-label
 * - 键盘可访问（Tab 切换）
 * - 当前选中项有清晰的视觉指示
 * - 使用 Lucide 线性图标
 */

import { MessageSquare, Bot, Wrench, Package, Settings, Sun, Moon, Activity, FlaskConical, ListRestart, Brain, Network, GitBranch, CalendarClock } from 'lucide-react';
import { useAppStore, type ViewType } from '../stores/appStore';
import { useSettingsStore } from '../stores/settingsStore';
import { cn } from '../utils/cn';

/** 导航项配置 */
interface NavItem {
  /** 视图标识 */
  id: ViewType;
  /** 显示名称 */
  label: string;
  /** 图标组件 */
  icon: React.ComponentType<{ size?: number; strokeWidth?: number }>;
}

/** 主要导航项。Record 穷尽 ViewType，避免像 MCP 那样漏入口。 */
const NAV_ITEMS: Record<ViewType, NavItem> = {
  chat: { id: 'chat', label: '对话', icon: MessageSquare },
  traces: { id: 'traces', label: '运行轨迹', icon: Activity },
  runs: { id: 'runs', label: '运行任务', icon: ListRestart },
  jobs: { id: 'jobs', label: '定时任务', icon: CalendarClock },
  sandbox: { id: 'sandbox', label: '测试沙盒', icon: FlaskConical },
  agents: { id: 'agents', label: 'Agents', icon: Bot },
  subagents: { id: 'subagents', label: 'Subagents', icon: Network },
  tools: { id: 'tools', label: '工具管理', icon: Wrench },
  mcp: { id: 'mcp', label: 'MCP', icon: GitBranch },
  skills: { id: 'skills', label: 'Skills', icon: Package },
  memory: { id: 'memory', label: 'Memory', icon: Brain },
  settings: { id: 'settings', label: '设置', icon: Settings },
};

/** 导航按钮样式 */
const navButtonStyle = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: '40px',
  height: '40px',
  borderRadius: 'var(--radius-lg)',
  border: 'none',
  cursor: 'pointer',
  fontSize: '18px',
  transition: 'background-color var(--duration-normal) var(--ease-out), color var(--duration-normal) var(--ease-out), transform var(--duration-normal) var(--ease-out)',
  WebkitAppRegion: 'no-drag', // Electron: 按钮区域不可拖拽
} as const;

/**
 * ActivityBar 组件
 */
export function ActivityBar() {
  const currentView = useAppStore((s) => s.currentView);
  const setView = useAppStore((s) => s.setView);
  const theme = useSettingsStore((s) => s.theme);
  const toggleTheme = useSettingsStore((s) => s.toggleTheme);

  return (
    <nav
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'space-between',
        height: '100%',
        padding: '8px 0',
        backgroundColor: 'var(--color-bg-secondary)',
        borderRight: '1px solid var(--color-border)',
      }}
      aria-label="主导航"
    >
      {/* 顶部导航项 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {Object.values(NAV_ITEMS).map((item) => {
          const isActive = currentView === item.id;
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              onClick={() => setView(item.id)}
              title={item.label}
              aria-label={item.label}
              aria-current={isActive ? 'page' : undefined}
              className={cn(
                isActive && 'active'
              )}
              style={{
                ...navButtonStyle,
                backgroundColor: isActive
                  ? 'var(--color-accent-bg)'
                  : 'transparent',
                color: isActive
                  ? 'var(--color-accent)'
                  : 'var(--color-text-tertiary)',
              }}
            >
              <Icon size={20} strokeWidth={1.5} />
            </button>
          );
        })}
      </div>

      {/* 底部主题切换 */}
      <button
        onClick={toggleTheme}
        title={theme === 'light' ? '切换到暗色模式' : '切换到亮色模式'}
        aria-label={theme === 'light' ? '切换到暗色模式' : '切换到亮色模式'}
        style={{
          ...navButtonStyle,
          backgroundColor: 'transparent',
          color: 'var(--color-text-tertiary)',
        }}
      >
        {theme === 'light' ? <Moon size={20} strokeWidth={1.5} /> : <Sun size={20} strokeWidth={1.5} />}
      </button>
    </nav>
  );
}
