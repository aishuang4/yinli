/**
 * TitleBar — Electron 自定义标题栏组件
 *
 * 在无边框窗口中提供拖拽区域和应用标题显示。
 * macOS 使用 hiddenInset 标题栏样式，红绿灯按钮由系统渲染。
 *
 * 注意：此区域需要设置 -webkit-app-region: drag 来支持窗口拖拽。
 */

import { GitBranch, PanelsTopLeft } from 'lucide-react';
import { UserMenu } from '../features/auth/UserMenu';
import { useAppStore, type WorkspaceMode } from '../stores/appStore';
import { isSurfaceEnabled } from '../stores/productFeatures';
import { useProductFeaturesStore } from '../stores/productFeaturesStore';
import { cn } from '../utils/cn';
import './TitleBar.css';

const MODE_OPTIONS: Array<{
  id: WorkspaceMode;
  label: string;
  icon: React.ComponentType<{ size?: number; strokeWidth?: number }>;
}> = [
  { id: 'workstation', label: '工作台', icon: PanelsTopLeft },
  { id: 'workflow', label: '任务流', icon: GitBranch },
];

export function TitleBar() {
  const workspaceMode = useAppStore((s) => s.workspaceMode);
  const setWorkspaceMode = useAppStore((s) => s.setWorkspaceMode);
  const workflowEnabled = useProductFeaturesStore((s) => isSurfaceEnabled(s.features, 'workflow'));
  const modeOptions = MODE_OPTIONS.filter((option) => option.id !== 'workflow' || workflowEnabled);
  const currentView = useAppStore((state) => state.currentView);
  const userCenterInPageHeader = workspaceMode === 'workstation' && currentView === 'chat';

  return (
    <div
      className="titlebar"
    >
      {modeOptions.length > 1 ? (
      <div className="titlebar__mode-switch" role="tablist" aria-label="切换产品工作空间">
        <span className="titlebar__mode-indicator" data-mode={workspaceMode} />
        {modeOptions.map((option) => {
          const active = option.id === workspaceMode;
          const Icon = option.icon;
          return (
            <button
              key={option.id}
              type="button"
              role="tab"
              aria-selected={active}
              className={cn('titlebar__mode-button', active && 'titlebar__mode-button--active')}
              onClick={() => setWorkspaceMode(option.id)}
            >
              <Icon size={14} strokeWidth={1.7} />
              <span>{option.label}</span>
            </button>
          );
        })}
      </div>
      ) : null}
      {userCenterInPageHeader ? null : (
        <div className="titlebar__account">
          <UserMenu className="icon-button h-8 w-8 rounded-lg" />
        </div>
      )}
    </div>
  );
}
