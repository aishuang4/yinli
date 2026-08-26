/** 群聊工作室 HTTP 客户端。不走 Chat SSE。 */

import type { GroupStudioSettings, GroupStudioSnapshot } from './types';

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
  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }
  return fallback;
}
