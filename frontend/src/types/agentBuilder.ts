import type { Agent, ModelConfig } from './agent';

export interface BuilderRecommendedItem {
  id: string;
  name: string;
  reason: string;
  matched: boolean;
}

export interface BuilderAgentDraft {
  tempId: string;
  name: string;
  description: string;
  systemPrompt: string;
  modelConfig: ModelConfig;
  mcpTools: string[];
  skills: string[];
  tags: string[];
  recommendedTools: BuilderRecommendedItem[];
  recommendedSkills: BuilderRecommendedItem[];
  missingTools: string[];
  missingSkills: string[];
}

export interface BuilderPlan {
  id: string;
  requirement: string;
  agents: BuilderAgentDraft[];
  warnings: string[];
}

export interface BuilderPlanPayload {
  requirement: string;
  preferredModel?: string;
}

export interface BuilderApplyResponse {
  createdAgents: Agent[];
  warnings: string[];
}
