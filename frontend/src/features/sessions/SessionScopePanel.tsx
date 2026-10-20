/** Session 工作目錄。技能由共享開關進入 Chat，不在這一行列出。 */

import { useEffect, useState } from 'react';
import { Checkbox, Select } from '@/components/design';
import { hostedDirectoryOptions, planDirectorySave, selectionFromServerError } from './sessionDirectory';

async function readErrorCode(response: Response): Promise<string> {
  const payload = await response.json().catch(() => null) as { detail?: { code?: string; message?: string } } | null;
  return payload?.detail?.code || '';
}

export function SessionScopePanel({ sessionId }: { sessionId: string }) {
  const desktop = typeof window !== 'undefined' && typeof window.electronAPI?.selectProjectDirectory === 'function';
  const [readOnly, setReadOnly] = useState(false);
  const [choices, setChoices] = useState(hostedDirectoryOptions([]));
  const [path, setPath] = useState('');
  const [note, setNote] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const sessionResponse = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
      if (sessionResponse.ok && !cancelled) {
        const session = await sessionResponse.json() as { workingDirectory?: string };
        setPath(session.workingDirectory || '');
      }
      if (!desktop) {
        const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/directories/choices`);
        if (response.ok && !cancelled) {
          const payload = await response.json() as { paths?: string[] };
          setChoices(hostedDirectoryOptions(payload.paths || []));
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [desktop, sessionId]);

  async function persistDirectory(nextPath: string, nextReadOnly: boolean) {
    const plan = planDirectorySave(nextPath ? { path: nextPath } : { canceled: true }, nextReadOnly);
    if (plan.action !== 'save' || plan.status !== 'selected') {
      setPath('');
      setNote('内部工作区');
      return;
    }
    const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/directories`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: plan.path, accessMode: plan.accessMode }),
    });
    if (!response.ok) {
      const rejected = selectionFromServerError(await readErrorCode(response));
      setNote(rejected.status === 'rejected' ? rejected.reason : '无法保存目录');
      return;
    }
    setPath(plan.path);
    setNote(plan.accessMode === 'read' ? '只读授权' : plan.path);
  }

  async function pickDesktop() {
    const result = await window.electronAPI?.selectProjectDirectory();
    if (!result || result.canceled || !result.path) return;
    await persistDirectory(result.path, readOnly);
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-secondary)]">
      {desktop ? (
        <button type="button" className="icon-button h-7 shrink-0 px-2" onClick={() => { void pickDesktop(); }}>
          工作目录
        </button>
      ) : (
        <Select
          aria-label="工作目录"
          className="max-w-[180px]"
          size="small"
          value={path}
          options={choices}
          onChange={(value) => { void persistDirectory(String(value ?? ''), readOnly); }}
        />
      )}
      {desktop ? (
        <span className="max-w-[180px] shrink-0 truncate" title={path || '内部工作区'}>{path || '内部工作区'}</span>
      ) : null}
      <Checkbox checked={readOnly} onChange={(event) => {
        setReadOnly(event.target.checked);
        if (path) void persistDirectory(path, event.target.checked);
      }}>
        只读
      </Checkbox>
      {note ? <span className="max-w-[120px] truncate" title={note}>{note}</span> : null}
    </div>
  );
}
