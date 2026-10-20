/**
 * TitleBar — Electron 自定义标题栏组件
 *
 * 在无边框窗口中提供拖拽区域和应用标题显示。
 * macOS 使用 hiddenInset 标题栏样式，红绿灯按钮由系统渲染。
 *
 * 注意：此区域需要设置 -webkit-app-region: drag 来支持窗口拖拽。
 */

import { useState } from 'react';
import { Boxes, FolderKanban, GitBranch, PanelsTopLeft, Plus } from 'lucide-react';
import { UserMenu } from '../features/auth/UserMenu';
import { Select, Tooltip } from '@/components/design';
import { useWorkspaceProjects } from '../hooks/useWorkspaceProjects';
import { ContextCreateModal } from '../features/workspaces/ContextCreateModal';
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
  const setActiveContext = useAppStore((s) => s.setActiveContext);
  const currentView = useAppStore((state) => state.currentView);
  const userCenterInPageHeader = workspaceMode === 'workstation' && currentView === 'chat';
  const [createOpen, setCreateOpen] = useState(false);
  const {
    workspaces,
    projects,
    activeWorkspaceId,
    activeProjectId,
    loading,
    error,
    selectWorkspace,
    selectProject,
  } = useWorkspaceProjects();

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
      {workspaceMode === 'workstation' && (
        <div className="titlebar__runtime-context" title={error || undefined}>
          <Tooltip title="Workspace 资源边界">
            <Boxes size={14} strokeWidth={1.7} className="titlebar__context-icon" />
          </Tooltip>
          <Select
            aria-label="选择 Workspace"
            className="titlebar__context-select titlebar__context-select--workspace"
            value={activeWorkspaceId || undefined}
            loading={loading}
            options={workspaces.map((workspace) => ({ label: workspace.name, value: workspace.id }))}
            onChange={(value) => void selectWorkspace(String(value))}
          />
          <span className="titlebar__context-separator" />
          <Tooltip title="当前 Project">
            <FolderKanban size={14} strokeWidth={1.7} className="titlebar__context-icon" />
          </Tooltip>
          <Select
            aria-label="选择 Project"
            className="titlebar__context-select titlebar__context-select--project"
            value={activeProjectId || undefined}
            loading={loading}
            disabled={projects.length === 0}
            options={projects.map((project) => ({ label: project.name, value: project.id }))}
            onChange={(value) => selectProject(String(value))}
          />
          <Tooltip title="新建 Workspace 或 Project" mode="dark">
            <button
              type="button"
              className="titlebar__context-create"
              aria-label="新建 Workspace 或 Project"
              onClick={() => setCreateOpen(true)}
            >
              <Plus size={15} strokeWidth={1.8} />
            </button>
          </Tooltip>
        </div>
      )}
      {userCenterInPageHeader ? null : (
        <div className="titlebar__account">
          <UserMenu className="icon-button h-8 w-8 rounded-lg" />
        </div>
      )}
      <ContextCreateModal
        open={createOpen}
        workspaceId={activeWorkspaceId}
        allowWorkspaceCreation
        onCancel={() => setCreateOpen(false)}
        onCreated={(workspaceId, projectId) => {
          setActiveContext(workspaceId, projectId);
          setCreateOpen(false);
        }}
      />
    </div>
  );
}
