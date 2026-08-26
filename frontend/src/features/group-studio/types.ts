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

export interface GroupStudioQuestion {
  id: string;
  text: string;
  askedBy: string;
  resolved: boolean;
  resolution: string;
}

export interface GroupStudioDecision {
  id: string;
  text: string;
}

export interface GroupStudioBlackboard {
  groupId: string;
  revision: number;
  goal: string;
  constraints: string[];
  decisions: GroupStudioDecision[];
  openQuestions: GroupStudioQuestion[];
  artifacts: string[];
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
  id: string;
  projectId: string;
  workspaceId: string;
  chatSessionId: string;
  title: string;
  status: GroupStudioStatus;
  phaseBeforePause?: string | null;
  taskCard: GroupStudioTaskCard;
  blackboard?: GroupStudioBlackboard;
  members: GroupStudioMember[];
  budget?: GroupStudioBudget;
  currentApproval?: GroupStudioApproval | null;
  messages?: GroupStudioMessage[];
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
