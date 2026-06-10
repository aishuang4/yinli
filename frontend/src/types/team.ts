export type TeamMemberRole = 'leader' | 'worker';
export type TeamStatus = 'idle' | 'running' | 'ended';
export type TeamExecutionMode = 'parallel' | 'depends_on';

export interface TeamMember {
  id: string;
  agentId: string;
  name: string;
  role: TeamMemberRole;
  description: string;
  executionMode: TeamExecutionMode;
  dependsOn: string[];
  handoffPrompt: string;
  status: string;
}

export interface TeamRecord {
  id: string;
  name: string;
  description: string;
  leaderAgentId: string;
  members: TeamMember[];
  status: TeamStatus;
  createdAt: string;
  updatedAt: string;
}

export interface TeamCreatePayload {
  name: string;
  description?: string;
  leaderAgentId: string;
  workerAgentIds: string[];
}

export interface TeamUpdatePayload {
  name?: string;
  description?: string;
  leaderAgentId?: string;
  workerAgentIds?: string[];
  members?: TeamMember[];
  status?: TeamStatus;
}
