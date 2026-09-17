/**
 * Skill 相关类型定义
 *
 * 与 backend/src/server/routes/skills.py 的 Pydantic 模型保持同步。
 */

export type SkillInstallStatus = 'available' | 'installed' | 'error';

export interface Skill {
  id: string;
  name: string;
  description: string;
  category: string;
  trigger: string;
  content: string;
  tags: string[];
  resourcePath: string;
  resources: string[];
  warnings?: string[];
  status: SkillInstallStatus;
  enabled: boolean;
  availableToAllAgents?: boolean;
  installedAt: string | null;
  updatedAt: string;
  workspaceId?: string;
}

export const LOCKED_SHARED_SKILL_ID = 'yinli-catalog';

export function isSkillShareLocked(skill: Pick<Skill, 'id'>): boolean {
  return skill.id === LOCKED_SHARED_SKILL_ID;
}

export function isSkillShared(skill: Pick<Skill, 'availableToAllAgents' | 'id'>): boolean {
  return isSkillShareLocked(skill) || skill.availableToAllAgents !== false;
}

export interface SkillRegisterPayload {
  name: string;
  description?: string;
  category?: string;
  trigger?: string;
  content?: string;
  tags?: string[];
  workspaceId?: string;
}
