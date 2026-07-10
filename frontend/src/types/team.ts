export type TeamMemberRole = 'leader' | 'member';
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
  memberAgentIds: string[];
}

export interface TeamUpdatePayload {
  name?: string;
  description?: string;
  leaderAgentId?: string;
  memberAgentIds?: string[];
  members?: TeamMember[];
  status?: TeamStatus;
}
