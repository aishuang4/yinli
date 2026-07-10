/** Team Runtime 独立前端领域模型。 */

export type TeamRunStatus = 'running' | 'completed' | 'failed' | 'cancelled';
export type TeamRunPhase = 'created' | 'coordinating' | 'executing' | 'synthesizing' | 'completed' | 'failed' | 'cancelled';
export type TeamMemberStatus = 'created' | 'idle' | 'running' | 'waiting' | 'failed' | 'stopped';
export type TeamTaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface TeamRunRecord {
  id: string;
  sessionId: string;
  chatRunId: string;
  goal: string;
  status: TeamRunStatus;
  phase: TeamRunPhase;
  leaderId: string;
  sourceTemplateId: string;
  sourceTemplateName: string;
  createdAt: string;
  updatedAt: string;
  completedAt: string;
  failureReason: string;
}

export interface TeamMemberRecord {
  id: string;
  teamRunId: string;
  sourceAgentId: string;
  name: string;
  role: string;
  status: TeamMemberStatus;
  sessionKey: string;
  activeTaskId: string;
  createdAt: string;
  updatedAt: string;
}

export interface TeamTaskRecord {
  id: string;
  teamRunId: string;
  title: string;
  status: TeamTaskStatus;
  assignedMemberId: string;
  ownerMemberId: string;
  dependsOn: string[];
  priority: number;
  blocked: boolean;
  briefPreview: string;
  resultPreview: string;
  failureReason: string;
  createdAt: string;
  updatedAt: string;
  completedAt: string;
}

export interface TeamMailRecord {
  id: string;
  teamRunId: string;
  from: string;
  to: string;
  kind: string;
  subject: string;
  content: string;
  taskId: string;
  createdAt: string;
}

export interface TeamSnapshot {
  teamRunId: string;
  run: TeamRunRecord;
  members: TeamMemberRecord[];
  tasks: TeamTaskRecord[];
  mailbox: TeamMailRecord[];
  counts: {
    members: number;
    tasks: number;
    queued: number;
    running: number;
    succeeded: number;
    failed: number;
    mail: number;
  };
  recentEvents?: TeamDomainEvent[];
  updatedAt: string;
}

export interface TeamDomainEvent {
  id: string;
  seq: number;
  teamRunId: string;
  chatRunId: string;
  type: string;
  actorId: string;
  taskId: string;
  status: string;
  timestamp: string;
  payload: Record<string, unknown>;
  snapshot?: TeamSnapshot;
}

export interface TeamConsoleState {
  teamRunId: string;
  snapshot: TeamSnapshot | null;
  events: TeamDomainEvent[];
}

export const EMPTY_TEAM_CONSOLE: TeamConsoleState = {
  teamRunId: '',
  snapshot: null,
  events: [],
};
