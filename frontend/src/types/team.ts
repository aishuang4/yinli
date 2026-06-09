export type TeamMemberRole = 'leader' | 'worker';
export type TeamStatus = 'idle' | 'running' | 'ended';

export interface TeamMember {
  id: string;
  agentId: string;
  name: string;
  role: TeamMemberRole;
  description: string;
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
