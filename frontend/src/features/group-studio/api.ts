/** 群聊工作室 HTTP 客户端。不走 Chat SSE。 */

import type {
  GroupStudioApproval,
  GroupStudioAgentOption,
  GroupStudioFile,
  GroupStudioListItem,
  GroupStudioMember,
  GroupStudioMessage,
  GroupStudioSettings,
  GroupStudioSnapshot,
} from './types';

export async function fetchStudioList(projectId: string): Promise<GroupStudioListItem[]> {
  const response = await fetch(`/api/group-studio?projectId=${encodeURIComponent(projectId)}`);
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
  return await response.json() as GroupStudioSnapshot | null;
}

export async function fetchGroupStudio(groupId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}`);
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法加载工作室'));
  }
  return await response.json() as GroupStudioSnapshot;
}

export async function fetchAvailableStudioAgents(groupId: string): Promise<GroupStudioAgentOption[]> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/available-agents`);
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法加载可加入的 Agent'));
  }
  return await response.json() as GroupStudioAgentOption[];
}

export async function updateStudioModel(groupId: string, projectId: string, modelConfigId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/model`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projectId, modelConfigId }),
  });
  if (!response.ok) throw new Error(await readDetail(response, '无法切换群聊模型'));
  return await response.json() as GroupStudioSnapshot;
}

export async function updateTaskCard(groupId: string, input: {
  projectId: string;
  goal?: string;
  background?: string;
  constraints?: string[];
  dodChecklist?: GroupStudioSnapshot['taskCard']['dodChecklist'];
}): Promise<GroupStudioSnapshot['taskCard']> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/task-card`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法更新任务卡'));
  }
  return await response.json() as GroupStudioSnapshot['taskCard'];
}

export async function confirmStudioIntake(
  groupId: string,
  projectId: string,
): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/confirm-intake`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projectId }),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法确认开始'));
  }
  return await response.json() as GroupStudioSnapshot;
}

export async function muteStudioMember(groupId: string, memberId: string, input: {
  projectId: string;
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

export async function removeStudioMember(groupId: string, memberId: string, input: {
  projectId: string;
}): Promise<GroupStudioMember> {
  const response = await fetch(
    `/api/group-studio/${encodeURIComponent(groupId)}/members/${encodeURIComponent(memberId)}/remove`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
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
  projectId: string,
  file: File,
): Promise<GroupStudioFile> {
  const body = new FormData();
  body.append('file', file);
  const response = await fetch(
    `/api/group-studio/${encodeURIComponent(groupId)}/files?projectId=${encodeURIComponent(projectId)}`,
    { method: 'POST', body },
  );
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法上传文件'));
  }
  return await response.json() as GroupStudioFile;
}

export async function postStudioMessage(groupId: string, input: {
  projectId: string;
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

export async function pauseStudio(groupId: string, projectId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/pause`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projectId }),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法暂停工作室'));
  }
  return await response.json() as GroupStudioSnapshot;
}

export async function resumeStudio(groupId: string, projectId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projectId }),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法恢复工作室'));
  }
  return await response.json() as GroupStudioSnapshot;
}

export async function interruptStudio(groupId: string, projectId: string): Promise<void> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/interrupt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projectId }),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法停止当前发言'));
  }
}

export async function closeStudio(groupId: string, projectId: string, confirm: boolean): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/close`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projectId, confirm }),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法结项'));
  }
  return await response.json() as GroupStudioSnapshot;
}

export async function deleteStudio(groupId: string, projectId: string): Promise<void> {
  const response = await fetch(
    `/api/group-studio/${encodeURIComponent(groupId)}?projectId=${encodeURIComponent(projectId)}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法删除群聊'));
  }
}

export async function startStudioReview(groupId: string, projectId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/start-review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projectId }),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法进入验收'));
  }
  return await response.json() as GroupStudioSnapshot;
}

export async function confirmStudioRestatement(groupId: string, projectId: string): Promise<GroupStudioSnapshot> {
  const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/confirm-restatement`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projectId }),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法确认复述'));
  }
  return await response.json() as GroupStudioSnapshot;
}

export async function decideStudioApproval(groupId: string, input: {
  projectId: string;
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
  projectId: string;
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
  projectId: string;
  chatSessionId: string;
  agentId: string;
  draftText: string;
  filePaths: string[];
  reuseBlankDraft?: boolean;
}): Promise<GroupStudioSnapshot> {
  const response = await fetch('/api/group-studio/from-chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await readDetail(response, '无法进入工作室'));
  }
  return await response.json() as GroupStudioSnapshot;
}

const GROUP_STUDIO_ERROR_TEXT: Record<string, string> = {
  chat_run_active: '请先停止或完成当前回复',
  group_studio_disabled: '群聊工作室未开启',
  cross_project_session: '当前会话属于其他 Project，无法升级为工作室',
  job_agent_forbidden: 'Job Agent 不能进入工作室',
  agent_not_bound: '当前 Agent 未绑定到此 Project',
  member_cap: '工作室人数已达上限',
  goal_required: '请先和主持人把目标说清楚',
  drafting_no_floor: '还在对谈中，主持人会继续问你',
  intake_not_confirmed: '确认开始后才能派发言或进入执行',
  intake_not_ready: '主持人还没有请你确认开始',
  intake_worker_forbidden: '对谈期工人还不能发言',
  host_required: '不能静音或移除主持人',
  open_questions_dropped: '未决问题不能从黑板上抹掉',
  invalid_upload: '文件过大或路径不合法',
  approval_not_pending: '这条审批已经处理过了',
  worker_session_lost: '工人会话已丢失，请重新发起这一轮',
  studio_paused: '工作室已暂停',
  studio_circuit_broken: '工作室已熔断，请先恢复',
  not_awaiting_restatement: '当前不需要确认复述',
  restatement_missing: '主持人还没有复述新目标',
  dod_incomplete: '请先勾齐验收清单再结项',
  illegal_transition: '当前阶段不能做这个操作',
  not_found: '找不到这个工作室或成员',
  member_not_found: '找不到这个成员',
  work_item_claimed: '这项工作已被认领',
  work_item_not_owned: '这项工作不属于当前工人',
  floor_not_granted: '现在还没轮到这个人发言',
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
