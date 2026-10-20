/** 群聊工作室前端类型，对齐 /api/group-studio camelCase JSON。 */

export type GroupStudioStatus = 'open' | 'paused' | 'done' | 'deleted';

export interface GroupStudioMember {
  id: string;
  groupId: string;
  role: 'worker' | 'host';
  agentId: string;
  displayName: string;
  status: 'active' | 'crashed' | 'removed';
  muted: boolean;
  joinedAt?: string;
}

export interface GroupStudioAgentOption {
  id: string;
  name: string;
  description: string;
}

export interface GroupStudioPin {
  title: string;
  goal: string;
  notes: string;
  updatedAt: string;
}

export interface GroupStudioFile {
  id: string;
  groupId: string;
  title: string;
  path: string;
  mediaType: string;
  byteSize: number;
  createdBy: string;
}

export interface GroupStudioMessage {
  id: string;
  speakerId: string;
  role: string;
  intent: string;
  body: string;
  thinking?: string;
  visibleInTimeline: boolean;
}

export interface GroupStudioApproval {
  id: string;
  groupId: string;
  runId: string;
  memberId: string;
  replyId: string;
  toolCalls: Array<{ id?: string; name?: string; input?: unknown }>;
  toolNames: string[];
  argumentsPreview: string;
  status: 'pending' | 'approved' | 'denied' | 'cancelled';
}

export interface GroupStudioBudget {
  maxTurns: number;
  maxNoProgressTurns: number;
  usedTurns: number;
  noProgressTurns: number;
}

export interface GroupStudioSnapshot {
  modelConfigId?: string;
  id: string;
  chatSessionId: string;
  title: string;
  status: GroupStudioStatus;
  pauseReason?: string | null;
  pin: GroupStudioPin;
  members: GroupStudioMember[];
  budget?: GroupStudioBudget;
  currentRunId?: string | null;
  currentSpeakerMemberId?: string | null;
  pendingSpeakerMemberId?: string | null;
  speakerQueue?: string[];
  lastTurn?: { kind: 'assign' | 'all_round'; mentions: string[] } | null;
  canRerun?: boolean;
  currentApproval?: GroupStudioApproval | null;
  messages?: GroupStudioMessage[];
  createdAt: string;
  updatedAt: string;
}

export interface GroupStudioSettings {
  enabled: boolean;
  maxMembers: number;
  maxParallelWorkers: number;
  maxTurns: number;
}

export interface GroupStudioListItem {
  id: string;
  title: string;
  status: GroupStudioStatus;
  chatSessionId: string;
  updatedAt: string;
}
