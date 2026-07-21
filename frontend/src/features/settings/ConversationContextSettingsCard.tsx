/** ConversationContextSettingsCard — 设置页中的全局对话 Workspace / Project 选择。 */

import { useState } from 'react';
import { Button, Card } from '@agentscope-ai/design';
import { Plus } from 'lucide-react';
import { SelectField } from '../../components/form';
import { useWorkspaceProjects } from '../../hooks/useWorkspaceProjects';
import { useAppStore } from '../../stores/appStore';
import { ContextCreateModal } from '../workspaces/ContextCreateModal';

export function ConversationContextSettingsCard() {
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
      <Card bodyStyle={{ padding: 0 }}>
        <div className="p-5">
          <div className="text-sm font-medium text-[var(--color-text-primary)]">对话上下文</div>
          <p className="mt-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
            新会话会使用这里选择的 Workspace 和 Project。
          </p>
          <div className="mt-4 space-y-4">
            <SelectField
              label="Workspace"
              value={activeWorkspaceId || undefined}
              loading={loading}
              options={workspaces.map((workspace) => ({ label: workspace.name, value: workspace.id }))}
              onChange={(value) => void selectWorkspace(String(value))}
            />
            <SelectField
              label="Project"
              value={activeProjectId || undefined}
              loading={loading}
              disabled={projects.length === 0}
              options={projects.map((project) => ({ label: project.name, value: project.id }))}
              onChange={(value) => selectProject(String(value))}
            />
            <Button className="w-full justify-center" size="small" icon={<Plus size={14} />} onClick={() => setCreateOpen(true)}>
              新建 Workspace 或 Project
            </Button>
          </div>
        </div>
      </Card>
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
