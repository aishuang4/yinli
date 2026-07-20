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
  status: SkillInstallStatus;
  enabled: boolean;
  installedAt: string | null;
  updatedAt: string;
  workspaceId?: string;
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
