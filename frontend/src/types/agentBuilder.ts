import type { Agent, ModelConfig } from './agent';
import type { TeamRecord } from './team';

export type BuilderMode = 'agents' | 'team';
export type BuilderAgentRole = 'leader' | 'worker';

export interface BuilderRecommendedItem {
  id: string;
  name: string;
  reason: string;
  matched: boolean;
}

export interface BuilderAgentDraft {
  tempId: string;
  name: string;
  role: BuilderAgentRole;
  description: string;
  systemPrompt: string;
  modelConfig: ModelConfig;
  mcpTools: string[];
  skills: string[];
  callableAgentRefs: string[];
  tags: string[];
  recommendedTools: BuilderRecommendedItem[];
  recommendedSkills: BuilderRecommendedItem[];
  missingTools: string[];
  missingSkills: string[];
}

export interface BuilderTeamDraft {
  name: string;
  description: string;
}

export interface BuilderPlan {
  id: string;
  requirement: string;
  mode: BuilderMode;
  team: BuilderTeamDraft | null;
  agents: BuilderAgentDraft[];
  warnings: string[];
}

export interface BuilderPlanPayload {
  requirement: string;
  mode: BuilderMode;
  preferredModel?: string;
}

export interface BuilderApplyResponse {
  createdAgents: Agent[];
  createdTeam: TeamRecord | null;
  warnings: string[];
}
