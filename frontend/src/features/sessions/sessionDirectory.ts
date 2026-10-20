/** Session 工作目錄選擇結果。取消、失效和只讀都不擴大授權。 */

export interface DirectoryDialogResult {
  canceled?: boolean;
  path?: string;
  accessMode?: string;
}

export type DirectorySelection =
  | { status: 'cancelled' }
  | { status: 'selected'; path: string; accessMode: 'read' | 'read_write' }
  | { status: 'rejected'; reason: string };

export function selectionFromDialog(result: DirectoryDialogResult | null | undefined): DirectorySelection {
  if (!result || result.canceled || !result.path) {
    return { status: 'cancelled' };
  }
  const accessMode = result.accessMode === 'read' ? 'read' : 'read_write';
  return { status: 'selected', path: result.path, accessMode };
}

export function selectionFromServerError(code: string): DirectorySelection {
  if (code === 'directory_unavailable') {
    return { status: 'rejected', reason: '目录已失效' };
  }
  if (code === 'resource_disabled') {
    return { status: 'rejected', reason: '全局资源已停用' };
  }
  if (code === 'agent_ineligible') {
    return { status: 'rejected', reason: '该 Agent 不能加入此会话' };
  }
  if (code === 'hosted_path_rejected' || code === 'illegal_mount') {
    return { status: 'rejected', reason: '服务端拒绝该目录' };
  }
  return { status: 'rejected', reason: code || '无法保存目录' };
}

export function planDirectorySave(
  result: DirectoryDialogResult | null | undefined,
  readOnly: boolean,
): DirectorySelection & { action: 'skip' | 'save' } {
  const selected = selectionFromDialog({
    ...result,
    accessMode: readOnly ? 'read' : result?.accessMode,
  });
  if (selected.status !== 'selected') {
    return { status: 'cancelled', action: 'skip' };
  }
  return { ...selected, action: 'save', accessMode: readOnly ? 'read' : selected.accessMode };
}

export function hostedDirectoryOptions(paths: string[]): Array<{ value: string; label: string }> {
  return [
    { value: '', label: '内部工作区' },
    ...paths.filter(Boolean).map((path) => ({ value: path, label: path })),
  ];
}

export function isReadOnlyGrant(accessMode: string): boolean {
  return accessMode === 'read';
}

export function canSwitchSession(isStreaming: boolean): boolean {
  return !isStreaming;
}
