/** Workspace / Project 创建弹窗。 */

import { useState } from 'react';
import { Modal } from '@agentscope-ai/design';
import { FolderOpen } from 'lucide-react';
import { FormError, TextField } from '../../components/form';
import type { ProjectRecord, WorkspaceRecord } from '../../types/workspace';
import { cn } from '../../utils/cn';

interface ContextCreateModalProps {
  open: boolean;
  workspaceId: string;
  allowWorkspaceCreation?: boolean;
  onCancel: () => void;
  onCreated: (workspaceId: string, projectId: string) => void;
}

async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function ContextCreateModal({
  open,
  workspaceId,
  allowWorkspaceCreation = false,
  onCancel,
  onCreated,
}: ContextCreateModalProps) {
  const [mode, setMode] = useState<'project' | 'workspace'>('project');
  const [workspaceName, setWorkspaceName] = useState('');
  const [projectDirectory, setProjectDirectory] = useState<{ name: string; path: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const reset = () => {
    setMode('project');
    setWorkspaceName('');
    setProjectDirectory(null);
    setError('');
  };

  const handleCancel = () => {
    if (submitting) return;
    reset();
    onCancel();
  };

  const handleSelectDirectory = async () => {
    if (!window.electronAPI) {
      setError('请在 Electron 桌面端选择 Project 文件夹。');
      return;
    }
    setError('');
    try {
      const result = await window.electronAPI.selectProjectDirectory();
      if (result.canceled || !result.path || !result.name) return;
      setProjectDirectory({ name: result.name, path: result.path });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Project 文件夹选择失败。');
    }
  };

  const handleCreate = async () => {
    if (!projectDirectory || (mode === 'workspace' && !workspaceName.trim())) {
      setError(mode === 'workspace' ? '请填写 Workspace 名称并选择 Project 文件夹。' : '请选择 Project 文件夹。');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const targetWorkspaceId = workspaceId;
      if (mode === 'workspace') {
        const created = await requestJson<{ workspace: WorkspaceRecord; project: ProjectRecord }>(
          '/api/workspaces/bootstrap',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              workspaceName: workspaceName.trim(),
              workspaceDescription: '',
              name: projectDirectory.name,
              description: '',
              rootPath: projectDirectory.path,
              instructionsFile: '',
            }),
          },
        );
        reset();
        onCreated(created.workspace.id, created.project.id);
        return;
      }
      const project = await requestJson<ProjectRecord>(
        `/api/workspaces/${encodeURIComponent(targetWorkspaceId)}/projects`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: projectDirectory.name,
            description: '',
            rootPath: projectDirectory.path,
            instructionsFile: '',
          }),
        },
      );
      reset();
      onCreated(targetWorkspaceId, project.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={allowWorkspaceCreation ? '新建工作上下文' : '新建 Project'}
      open={open}
      onCancel={handleCancel}
      onOk={() => void handleCreate()}
      confirmLoading={submitting}
      okText="创建"
      cancelText="取消"
      width={560}
    >
      <div className="space-y-4">
        {allowWorkspaceCreation && (
          <div className="grid grid-cols-2 gap-1 rounded-md bg-[var(--color-bg-secondary)] p-1" role="tablist">
            {([
              ['project', 'Project'],
              ['workspace', 'Workspace + Project'],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={mode === value}
                className={cn(
                  'h-8 rounded-md text-xs font-medium text-[var(--color-text-secondary)]',
                  mode === value && 'bg-[var(--color-bg-primary)] text-[var(--color-text-primary)] shadow-sm',
                )}
                onClick={() => setMode(value)}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        <FormError>{error}</FormError>
        {allowWorkspaceCreation && mode === 'workspace' && (
          <TextField
            label="Workspace 名称"
            required
            value={workspaceName}
            onChange={(event) => setWorkspaceName(event.target.value)}
            maxLength={80}
          />
        )}
        <div>
          <div className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">Project 文件夹 *</div>
          <button
            type="button"
            className="flex min-h-14 w-full items-center gap-3 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
            onClick={() => void handleSelectDirectory()}
          >
            <FolderOpen size={18} className="shrink-0 text-[var(--color-text-secondary)]" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-[var(--color-text-primary)]">
                {projectDirectory?.name || '选择文件夹'}
              </span>
              {projectDirectory && (
                <span className="mt-0.5 block truncate text-xs text-[var(--color-text-tertiary)]">
                  {projectDirectory.path}
                </span>
              )}
            </span>
          </button>
        </div>
      </div>
    </Modal>
  );
}
