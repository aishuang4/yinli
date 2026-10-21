/** 设置页里的当前 Project，以及目录授权的增删。 */

import { useState } from 'react';
import { Button, Card } from '@/components/design';
import { Plus } from 'lucide-react';
import { SelectField } from '../../components/form';
import { ProjectCreateModal } from '../projects/ProjectCreateModal';
import { useProjects } from '../../hooks/useProjects';

export function ConversationContextSettingsCard() {
  const [createOpen, setCreateOpen] = useState(false);
  const [folderPath, setFolderPath] = useState('');
  const [accessMode, setAccessMode] = useState<'read' | 'readwrite'>('read');
  const [folderError, setFolderError] = useState('');
  const {
    activeProjects,
    activeProject,
    activeProjectSlug,
    loading,
    selectProject,
    createProject,
    addFolder,
    removeFolder,
  } = useProjects();

  const handleAddFolder = async () => {
    const path = folderPath.trim();
    if (!path || !activeProject) return;
    setFolderError('');
    try {
      await addFolder(activeProject.slug, path, accessMode);
      setFolderPath('');
    } catch (err) {
      setFolderError(err instanceof Error ? err.message : '追加目录失败');
    }
  };

  return (
    <>
      <Card styles={{ body: { padding: 0 } }}>
        <div className="p-5">
          <div className="text-sm font-medium text-[var(--color-text-primary)]">当前 Project</div>
          <p className="mt-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
            新会话、Trace、记忆和定时任务都按这里选择的 Project 过滤。未选择时使用默认项目。
          </p>
          <div className="mt-4 space-y-4">
            <SelectField
              label="Project"
              value={activeProjectSlug || 'default'}
              loading={loading}
              options={activeProjects.map((project) => ({ label: project.name, value: project.slug }))}
              onChange={(value) => selectProject(String(value))}
            />
            <div className="text-xs text-[var(--color-text-tertiary)]">
              主目录：{activeProject?.primaryPath || '默认项目'}
            </div>
            <div className="space-y-2">
              {(activeProject?.folders || []).map((folder) => (
                <div key={folder.path} className="flex items-center gap-2 text-xs">
                  <span className="min-w-0 flex-1 truncate">{folder.path}</span>
                  <span className="shrink-0 text-[var(--color-text-tertiary)]">
                    {folder.isPrimary ? '主目录' : folder.accessMode}
                  </span>
                  {folder.isPrimary ? null : (
                    <Button
                      size="small"
                      onClick={() => void removeFolder(activeProjectSlug, folder.path).catch((err: unknown) => {
                        setFolderError(err instanceof Error ? err.message : '移除目录失败');
                      })}
                    >
                      移除
                    </Button>
                  )}
                </div>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2 text-sm"
                value={folderPath}
                placeholder="追加授权目录"
                onChange={(event) => setFolderPath(event.target.value)}
              />
              <SelectField
                label="权限"
                value={accessMode}
                options={[
                  { label: '只读', value: 'read' },
                  { label: '读写', value: 'readwrite' },
                ]}
                onChange={(value) => setAccessMode(value === 'readwrite' ? 'readwrite' : 'read')}
              />
              <Button size="small" onClick={() => void handleAddFolder()}>追加</Button>
            </div>
            {folderError ? <div className="text-xs text-[var(--color-error)]">{folderError}</div> : null}
            <Button className="w-full justify-center" size="small" icon={<Plus size={14} />} onClick={() => setCreateOpen(true)}>
              新建 Project
            </Button>
          </div>
        </div>
      </Card>
      <ProjectCreateModal
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onCreate={async (payload) => {
          await createProject(payload);
          setCreateOpen(false);
        }}
      />
    </>
  );
}
