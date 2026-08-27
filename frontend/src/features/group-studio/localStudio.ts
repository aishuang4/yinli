/** 未落库的本地草稿工作室。有目标/成员/验收/消息/文件等操作后再 from-chat。 */

import type { GroupStudioSnapshot } from './types';

export const LOCAL_STUDIO_PREFIX = 'gstu_local_';

export interface LocalStudioSeed {
  chatSessionId: string;
  agentId: string;
}

export function isLocalStudioId(groupId: string | null | undefined): boolean {
  return Boolean(groupId?.startsWith(LOCAL_STUDIO_PREFIX));
}

export function createLocalStudioId(): string {
  return `${LOCAL_STUDIO_PREFIX}${crypto.randomUUID().replace(/-/g, '').slice(0, 16)}`;
}

export function createLocalStudioSnapshot(input: {
  id: string;
  projectId: string;
  workspaceId: string;
  chatSessionId: string;
  agentId?: string;
  agentName?: string;
}): GroupStudioSnapshot {
  const now = new Date().toISOString();
  const taskId = `gtask_local_${input.id.slice(-8)}`;
  const hostId = `gmbr_local_host_${input.id.slice(-8)}`;
  const workerId = `gmbr_local_worker_${input.id.slice(-8)}`;
  const agentId = input.agentId || 'default';
  const agentName = input.agentName || (agentId === 'default' ? '默认助手' : agentId);
  return {
    id: input.id,
    projectId: input.projectId,
    workspaceId: input.workspaceId,
    chatSessionId: input.chatSessionId,
    title: '未命名工作室',
    status: 'drafting',
    phaseBeforePause: null,
    taskCard: {
      id: taskId,
      groupId: input.id,
      revision: 1,
      status: 'draft',
      goal: '',
      background: '',
      constraints: [],
      seedFromChat: false,
      dodChecklist: [],
    },
    blackboard: {
      groupId: input.id,
      revision: 1,
      goal: '',
      constraints: [],
      decisions: [],
      openQuestions: [],
      artifacts: [],
    },
    members: [
      {
        id: hostId,
        groupId: input.id,
        role: 'host',
        agentId: 'group-studio-host',
        displayName: '主持人',
        status: 'active',
        muted: false,
      },
      {
        id: workerId,
        groupId: input.id,
        role: 'worker',
        agentId: agentId,
        displayName: agentName,
        status: 'active',
        muted: false,
      },
    ],
    budget: {
      maxTurns: 40,
      maxNoProgressTurns: 3,
      usedTurns: 0,
      noProgressTurns: 0,
    },
    currentRunId: null,
    currentApproval: null,
    messages: [],
    awaitingRestatement: false,
    createdAt: now,
    updatedAt: now,
  };
}
