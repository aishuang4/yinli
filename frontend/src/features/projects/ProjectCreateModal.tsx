/** 新建 Project。名称创建后不可改，slug 由名称派生。 */

import { useState } from 'react';
import { Button, Modal } from '@/components/design';
import { FolderOpen } from 'lucide-react';

interface ProjectCreateModalProps {
  open: boolean;
  onCancel: () => void;
  onCreate: (payload: { name: string; primaryPath?: string }) => Promise<void>;
}

export function ProjectCreateModal({ open, onCancel, onCreate }: ProjectCreateModalProps) {
  const [name, setName] = useState('');
  const [primaryPath, setPrimaryPath] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const reset = () => {
    setName('');
    setPrimaryPath('');
    setError('');
  };

  const handleCancel = () => {
    if (submitting) return;
    reset();
    onCancel();
  };

  const handleSelectDirectory = async () => {
    if (!window.electronAPI) {
      setError('浏览器里请直接填写目录路径。');
      return;
    }
    try {
      const result = await window.electronAPI.selectProjectDirectory();
      if (result.canceled || !result.path) return;
      setPrimaryPath(result.path);
      if (!name.trim() && result.name) setName(result.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : '目录选择失败');
    }
  };

  const handleCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError('请填写 Project 名称。');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      await onCreate({ name: trimmed, primaryPath: primaryPath.trim() || undefined });
      reset();
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title="新建 Project"
      onCancel={handleCancel}
      onOk={() => void handleCreate()}
      okText="创建"
      confirmLoading={submitting}
    >
      <div className="space-y-3">
        <label className="block text-xs text-[var(--color-text-secondary)]">
          名称
          <input
            className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2 text-sm"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="block text-xs text-[var(--color-text-secondary)]">
          主目录
          <div className="mt-1 flex gap-2">
            <input
              className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2 text-sm"
              value={primaryPath}
              placeholder="留空则使用默认目录"
              onChange={(event) => setPrimaryPath(event.target.value)}
            />
            <Button size="small" icon={<FolderOpen size={14} />} onClick={() => void handleSelectDirectory()}>
              选择
            </Button>
          </div>
        </label>
        {error ? <div className="text-xs text-[var(--color-error)]">{error}</div> : null}
      </div>
    </Modal>
  );
}
