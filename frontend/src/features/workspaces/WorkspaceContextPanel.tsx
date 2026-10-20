/** 管理页侧栏中的 Workspace / Project 上下文入口。 */

import { useState } from 'react';
import { Select, Tooltip } from '@/components/design';
import { Plus } from 'lucide-react';
import { useWorkspaceProjects } from '../../hooks/useWorkspaceProjects';
import { useAppStore } from '../../stores/appStore';
import { ContextCreateModal } from './ContextCreateModal';

export function WorkspaceContextPanel() {
  const [createOpen, setCreateOpen] = useState(false);
  const setActiveContext = useAppStore((state) => state.setActiveContext);
  const {
    workspaces,
    projects,
    activeWorkspaceId,
    activeProjectId,
    loading,
    selectWorkspace,
    selectProject,
  } = useWorkspaceProjects();

  return (
    <>
      <div className="workbench-context-panel">
        <div className="workbench-context-panel__row">
          <Select
            aria-label="选择管理 Workspace"
            value={activeWorkspaceId || undefined}
            loading={loading}
            options={workspaces.map((workspace) => ({ label: workspace.name, value: workspace.id }))}
            onChange={(value) => void selectWorkspace(String(value))}
            style={{ minWidth: 0, flex: 1 }}
          />
          <Tooltip title="新建 Workspace 或 Project" mode="dark">
            <button
              type="button"
              className="workbench-context-panel__create"
              aria-label="新建 Workspace 或 Project"
              onClick={() => setCreateOpen(true)}
            >
              <Plus size={15} strokeWidth={1.8} />
            </button>
          </Tooltip>
        </div>
        <Select
          aria-label="选择管理 Project"
          value={activeProjectId || undefined}
          loading={loading}
          disabled={projects.length === 0}
          options={projects.map((project) => ({ label: project.name, value: project.id }))}
          onChange={(value) => selectProject(String(value))}
          style={{ width: '100%' }}
        />
      </div>
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
    </>
  );
}
