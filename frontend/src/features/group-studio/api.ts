/** 群聊工作室 HTTP 客户端。不走 Chat SSE。 */

import type {
  GroupStudioApproval,
  GroupStudioFile,
  GroupStudioMember,
  GroupStudioSettings,
  GroupStudioSnapshot,
} from './types';

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

export async function updateTaskCard(groupId: string, input: {
  projectId: string;
  goal?: string;
  background?: string;
  constraints?: string[];
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
  projectId: string;
  chatSessionId: string;
  agentId: string;
  draftText: string;
  filePaths: string[];
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

export async function readDetail(response: Response, fallback: string): Promise<string> {
  const payload = await response.json().catch(() => ({})) as { detail?: unknown };
  const detail = payload.detail;
  if (detail === 'chat_run_active') {
    return '请先停止或完成当前回复';
  }
  if (detail === 'group_studio_disabled') {
    return '群聊工作室未开启';
  }
  if (detail === 'cross_project_session') {
    return '当前会话属于其他 Project，无法升级为工作室';
  }
  if (detail === 'job_agent_forbidden') {
    return 'Job Agent 不能进入工作室';
  }
  if (detail === 'agent_not_bound') {
    return '当前 Agent 未绑定到此 Project';
  }
  if (detail === 'member_cap') {
    return '工作室人数已达上限';
  }
  if (detail === 'goal_required') {
    return '请先填写任务目标';
  }
  if (detail === 'drafting_no_floor') {
    return '还没有目标，主持人不能开始发言';
  }
  if (detail === 'host_required') {
    return '不能静音或移除主持人';
  }
  if (detail === 'open_questions_dropped') {
    return '未决问题不能从黑板上抹掉';
  }
  if (detail === 'invalid_upload') {
    return '文件过大或路径不合法';
  }
  if (detail === 'approval_not_pending') {
    return '这条审批已经处理过了';
  }
  if (detail === 'worker_session_lost') {
    return '工人会话已丢失，请重新发起这一轮';
  }
  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }
  return fallback;
}
