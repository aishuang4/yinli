/** 群聊工作室 HTTP 客户端。不走 Chat SSE。 */

import type {
  GroupStudioApproval,
  GroupStudioAgentOption,
  GroupStudioFile,
  GroupStudioListItem,
  GroupStudioMember,
  GroupStudioMessage,
  GroupStudioPin,
  GroupStudioSettings,
  GroupStudioSnapshot,
} from './types';

export async function fetchStudioList(): Promise<GroupStudioListItem[]> {
  const response = await fetch('/api/group-studio');
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法加载群聊列表'));
  }
  const payload = await response.json() as { studios?: GroupStudioListItem[] };
  return payload.studios ?? [];
}

export async function fetchGroupStudioSettings(): Promise<GroupStudioSettings> {
  const response = await fetch('/api/settings/group-studio');
  if (!response.ok) {
    throw new Error(await readDetail(response, '工作室设置加载失败'));
  }
  return await response.json() as GroupStudioSettings;
}

export async function fetchStudioByChat(sessionId: string): Promise<GroupStudioSnapshot | null> {
  const response = await fetch(`/api/group-studio/by-chat/${encodeURIComponent(sessionId)}`);
  if (response.status === 404) {
    const payload = await response.json().catch(() => ({})) as { detail?: string };
    if (payload.detail === 'group_studio_disabled') {
      throw new Error('group_studio_disabled');
    }
    return null;
  }
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法查询工作室'));
  }
  const payload = await response.json() as GroupStudioSnapshot | null;
  return payload ? normalizeSnapshot(payload) : null;
}

export async function fetchGroupStudio(groupId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}`);
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法加载工作室'));
  }
  return normalizeSnapshot(await response.json());
}

export async function fetchAvailableStudioAgents(groupId: string): Promise<GroupStudioAgentOption[]> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/available-agents`);
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法加载可加入的 Agent'));
  }
  return await response.json() as GroupStudioAgentOption[];
}

export async function updateStudioModel(groupId: string, modelConfigId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/model`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ modelConfigId }),
  });
  if (!response.ok) throw new Error(await readDetail(response, '无法切换群聊模型'));
  return normalizeSnapshot(await response.json());
}

export async function updateStudioPin(groupId: string, input: {
  goal?: string;
  background?: string;
}): Promise<GroupStudioPin> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/pin`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法更新置顶备注'));
  }
  const card = await response.json() as { goal?: string; background?: string; updatedAt?: string };
  return {
    title: '',
    goal: card.goal || '',
    notes: card.background || '',
    updatedAt: card.updatedAt || '',
  };
}

export async function muteStudioMember(groupId: string, memberId: string, input: {
  muted: boolean;
}): Promise<GroupStudioMember> {
  const response = await fetch(
    `/api/group-studio/${encodeURIComponent(groupId)}/members/${encodeURIComponent(memberId)}/mute`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
  );
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法静音成员'));
  }
  return await response.json() as GroupStudioMember;
}

export async function removeStudioMember(groupId: string, memberId: string): Promise<GroupStudioMember> {
  const response = await fetch(
    `/api/group-studio/${encodeURIComponent(groupId)}/members/${encodeURIComponent(memberId)}/remove`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
  );
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法移除成员'));
  }
  return await response.json() as GroupStudioMember;
}

export async function fetchStudioFiles(groupId: string): Promise<GroupStudioFile[]> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/files`);
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法加载文件库'));
  }
  const payload = await response.json() as { files?: GroupStudioFile[] };
  return payload.files ?? [];
}

export async function downloadStudioFile(groupId: string, artifactId: string, filename?: string): Promise<void> {
  const response = await fetch(
    `/api/group-studio/${encodeURIComponent(groupId)}/files/${encodeURIComponent(artifactId)}/download`,
  );
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法下载文件'));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename || getDownloadFilename(response.headers.get('Content-Disposition')) || 'download';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function getDownloadFilename(contentDisposition: string | null): string | undefined {
  if (!contentDisposition) return undefined;
  const encoded = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = contentDisposition.match(/filename="?([^";]+)"?/i)?.[1];
  let candidate = plain;
  if (encoded) {
    try {
      candidate = decodeURIComponent(encoded);
    } catch {
      candidate = plain;
    }
  }
  return candidate?.replace(/[\\/\0]/g, '').trim() || undefined;
}

export async function uploadStudioFile(
  groupId: string,
  file: File,
): Promise<GroupStudioFile> {
  const body = new FormData();
  body.append('file', file);
  const response = await fetch(
    `/api/group-studio/${encodeURIComponent(groupId)}/files`,
    { method: 'POST', body },
  );
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法上传文件'));
  }
  return await response.json() as GroupStudioFile;
}

export async function postStudioMessage(groupId: string, input: {
  body: string;
  mentions?: string[];
}): Promise<GroupStudioMessage> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法发送消息'));
  }
  return await response.json() as GroupStudioMessage;
}

export async function pauseStudio(groupId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/pause`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法暂停工作室'));
  }
  return normalizeSnapshot(await response.json());
}

export async function resumeStudio(groupId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法恢复工作室'));
  }
  return normalizeSnapshot(await response.json());
}

export async function rerunStudio(groupId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/rerun`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法再来一轮'));
  }
  return normalizeSnapshot(await response.json());
}

export async function interruptStudio(groupId: string): Promise<void> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/interrupt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法停止当前发言'));
  }
}

export async function closeStudio(groupId: string, confirm: boolean): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/close`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm }),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法结束群聊'));
  }
  return normalizeSnapshot(await response.json());
}

export async function deleteStudio(groupId: string): Promise<void> {
  const response = await fetch(
    `/api/group-studio/${encodeURIComponent(groupId)}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法删除群聊'));
  }
}

export async function decideStudioApproval(groupId: string, input: {
  approvalId: string;
  decision: 'approve' | 'deny';
}): Promise<GroupStudioApproval> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/approvals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法处理审批'));
  }
  return await response.json() as GroupStudioApproval;
}

export async function addStudioMember(groupId: string, input: {
  agentId: string;
}): Promise<GroupStudioMember> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/members`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法加人'));
  }
  return await response.json() as GroupStudioMember;
}

export async function createStudioFromChat(input: {
  modelConfigId?: string;
  chatSessionId: string;
  agentId: string;
  draftText?: string;
  filePaths?: string[];
  title?: string;
}): Promise<GroupStudioSnapshot> {
  const response = await fetch('/api/group-studio/from-chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法进入工作室'));
  }
  return normalizeSnapshot(await response.json());
}

const GROUP_STUDIO_ERROR_TEXT: Record<string, string> = {
  chat_run_active: '请先停止或完成当前回复',
  group_studio_disabled: '群聊工作室未开启',
  cross_project_session: '当前会话无法升级为工作室',
  job_agent_forbidden: 'Job Agent 不能进入工作室',
  default_agent_forbidden: '系统默认助手不能入群，请用普通 Chat Agent',
  agent_not_bound: '当前 Agent 不可用',
  member_cap: '工作室人数已达上限',
  already_member: '这个人已经在群里',
  no_eligible_speaker: '点到的人现在不能发言（已静音、已移出或不在群里）。',
  no_last_turn: '还没有上一轮可以重来',
  studio_busy: '现在还有人在说或队列没走完',
  studio_not_open: '这间群聊已经结束',
  empty_body: '请先写点内容再发送',
  invalid_upload: '文件过大或路径不合法',
  approval_not_pending: '这条审批已经处理过了',
  worker_session_lost: '工人会话已丢失，请重新发起这一轮',
  studio_paused: '工作室已暂停',
  illegal_transition: '现在不能做这个操作',
  not_found: '找不到这个工作室或成员',
  model_not_configured: '还没有配置可用模型',
  approval_batch_incomplete: '还有审批未处理完',
};

export function friendlyGroupStudioError(code: string, fallback = ''): string {
  const key = code.trim();
  return GROUP_STUDIO_ERROR_TEXT[key] || fallback || key;
}

export async function readDetail(response: Response, fallback: string): Promise<string> {
  const payload = await response.json().catch(() => ({})) as { detail?: unknown };
  const detail = payload.detail;
  if (typeof detail === 'string' && detail.trim()) {
    return friendlyGroupStudioError(detail, fallback);
  }
  return fallback;
}

function normalizeSnapshot(raw: GroupStudioSnapshot & {
  taskCard?: { goal?: string; background?: string };
  pin?: GroupStudioPin;
}): GroupStudioSnapshot {
  const pin = raw.pin ?? {
    title: raw.title || '',
    goal: raw.taskCard?.goal || '',
    notes: raw.taskCard?.background || '',
    updatedAt: raw.updatedAt,
  };
  return { ...raw, pin };
}
