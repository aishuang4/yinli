/** 群聊工作室前端类型，对齐 /api/group-studio camelCase JSON。 */

export type GroupStudioStatus =
  | 'drafting'
  | 'discussing'
  | 'executing'
  | 'reviewing'
  | 'awaiting_human'
  | 'paused'
  | 'circuit_broken'
  | 'closing'
  | 'archived'
  | 'failed';

export interface GroupStudioMember {
  id: string;
  groupId: string;
  role: 'host' | 'worker';
  agentId: string;
  displayName: string;
  status: 'active' | 'crashed' | 'removed';
  muted: boolean;
}

export interface GroupStudioTaskCard {
  id: string;
  groupId: string;
  revision: number;
  status: 'draft' | 'active' | 'frozen_for_close' | 'archived';
  goal: string;
  background: string;
  constraints: string[];
  seedFromChat: boolean;
}

export interface GroupStudioSnapshot {
  id: string;
  projectId: string;
  workspaceId: string;
  chatSessionId: string;
  title: string;
  status: GroupStudioStatus;
  taskCard: GroupStudioTaskCard;
  members: GroupStudioMember[];
  awaitingRestatement: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface GroupStudioSettings {
  enabled: boolean;
  maxMembers: number;
  maxParallelWorkers: number;
  maxTurns: number;
}
