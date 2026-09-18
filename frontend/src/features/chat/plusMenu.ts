/** Chat 发送框 + 菜单的分类与清单纯函数。 */

export const COMPOSER_STATUS_CHIP_CLASS =
  'inline-flex h-8 max-w-56 items-center gap-1.5 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-2 text-sm leading-none text-[var(--color-text-secondary)]';

import type { Skill } from '../../types/skill';
import type { ComposerAgentOption } from './ChatAgentSelect';
import { filterSkillsBySlashQuery, matchPlusMenuAction } from './forcedSkills';

export type PlusMenuCategory = 'files' | 'modes' | 'agents' | 'skills';

export type PlusMenuItem =
  | { kind: 'image' }
  | { kind: 'file' }
  | { kind: 'plan' }
  | { kind: 'think' }
  | { kind: 'agent-mode' }
  | { kind: 'team-mode' }
  | { kind: 'agent'; agent: ComposerAgentOption }
  | { kind: 'skill'; skill: Skill };

export const PLUS_MENU_CATEGORY_LABELS: Record<PlusMenuCategory, string> = {
  files: '添加文件',
  modes: '模式',
  agents: 'Agent',
  skills: '技能',
};

export const PLUS_MENU_SEARCH_PLACEHOLDERS: Record<PlusMenuCategory, string> = {
  files: '搜索文件',
  modes: '搜索模式',
  agents: '搜索 Agent',
  skills: '搜索技能',
};

export function buildPlusMenuCategories({
  skillsEnabled,
}: {
  skillsEnabled: boolean;
}): PlusMenuCategory[] {
  const categories: PlusMenuCategory[] = ['files', 'modes', 'agents'];
  if (skillsEnabled) categories.push('skills');
  return categories;
}

export function filterPlusMenuAgents(
  agents: ComposerAgentOption[],
  query: string,
): ComposerAgentOption[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return agents;
  return agents.filter((agent) => (
    agent.name.toLowerCase().includes(needle)
    || agent.id.toLowerCase().includes(needle)
    || agent.description.toLowerCase().includes(needle)
  ));
}

export function buildPlusMenuItems({
  category,
  query,
  showPlan,
  showTeam,
  skillsEnabled,
  skills,
  agents,
}: {
  category: PlusMenuCategory;
  query: string;
  showPlan: boolean;
  showTeam: boolean;
  skillsEnabled: boolean;
  skills: Skill[];
  agents: ComposerAgentOption[];
}): PlusMenuItem[] {
  if (category === 'files') {
    const items: PlusMenuItem[] = [];
    if (matchPlusMenuAction('image', query)) items.push({ kind: 'image' });
    if (matchPlusMenuAction('file', query)) items.push({ kind: 'file' });
    return items;
  }
  if (category === 'modes') {
    const items: PlusMenuItem[] = [];
    if (showPlan && matchPlusMenuAction('plan', query)) items.push({ kind: 'plan' });
    if (matchPlusMenuAction('think', query)) items.push({ kind: 'think' });
    if (showTeam && matchPlusMenuAction('agent-mode', query)) items.push({ kind: 'agent-mode' });
    if (showTeam && matchPlusMenuAction('team-mode', query)) items.push({ kind: 'team-mode' });
    return items;
  }
  if (category === 'agents') {
    return filterPlusMenuAgents(agents, query).map((agent) => ({ kind: 'agent', agent }));
  }
  if (!skillsEnabled) return [];
  return filterSkillsBySlashQuery(skills, query).map((skill) => ({ kind: 'skill', skill }));
}

export function plusMenuItemKey(item: PlusMenuItem): string {
  if (item.kind === 'skill') return `skill:${item.skill.id}`;
  if (item.kind === 'agent') return `agent:${item.agent.id}`;
  return item.kind;
}
