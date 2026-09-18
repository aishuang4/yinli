/** Chat 发送框强制开启 Skill 的纯函数。 */

import type { Skill } from '../../types/skill';

export const MAX_FORCED_SKILLS = 3;

export function filterComposerSkills(
  skills: Skill[],
  projectSkillIds: ReadonlySet<string>,
): Skill[] {
  return skills.filter((skill) => {
    if (!skill.enabled) return false;
    if (projectSkillIds.size === 0) return true;
    if (projectSkillIds.has(skill.id)) return true;
    if (skill.sourceScope === 'project') return true;
    return skill.availableToAllAgents !== false;
  });
}

export function toggleForcedSkillId(
  selected: string[],
  skillId: string,
  max = MAX_FORCED_SKILLS,
): string[] {
  if (selected.includes(skillId)) {
    return selected.filter((id) => id !== skillId);
  }
  if (selected.length >= max) return selected;
  return [...selected, skillId];
}

export type SlashQuery = {
  start: number;
  end: number;
  query: string;
};

/** 光标所在 token 以 `/` 开头且不是路径时，视为 Skill 斜杠命令。 */
export function readSlashQuery(value: string, cursor: number): SlashQuery | null {
  const pos = Math.max(0, Math.min(cursor, value.length));
  let start = pos;
  while (start > 0 && !/\s/.test(value[start - 1] ?? '')) {
    start -= 1;
  }
  if (value[start] !== '/') {
    return null;
  }
  let end = start + 1;
  while (end < value.length && !/\s/.test(value[end] ?? '')) {
    end += 1;
  }
  if (pos < start || pos > end) {
    return null;
  }
  const token = value.slice(start, end);
  if (token.indexOf('/', 1) !== -1) {
    return null;
  }
  return { start, end, query: token.slice(1) };
}

export function filterSkillsBySlashQuery(skills: Skill[], query: string): Skill[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return skills;
  return skills.filter((skill) => (
    skill.name.toLowerCase().includes(needle)
    || skill.id.toLowerCase().includes(needle)
    || skill.description.toLowerCase().includes(needle)
    || skill.trigger.toLowerCase().includes(needle)
  ));
}

export type PlusMenuAction = 'plan' | 'think' | 'agent-mode' | 'team-mode' | 'image' | 'file';

const PLUS_MENU_ACTION_LABELS: Record<PlusMenuAction, string[]> = {
  plan: ['计划', 'plan', '计划模式'],
  think: ['深度思考', 'think', '思考', '推理'],
  'agent-mode': ['单 agent', '单agent', 'agent'],
  'team-mode': ['team', '团队', '协作'],
  image: ['图片', 'image', '上传图片', '图'],
  file: ['文件', 'file', '附件', '上传文件'],
};

/** + 菜单动作是否匹配搜索词。 */
export function matchPlusMenuAction(action: PlusMenuAction, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return PLUS_MENU_ACTION_LABELS[action].some((label) => label.toLowerCase().includes(needle));
}

export function consumeSlashQuery(value: string, slash: SlashQuery): string {
  const before = value.slice(0, slash.start);
  const after = value.slice(slash.end);
  if (before.endsWith(' ') && after.startsWith(' ')) {
    return before + after.slice(1);
  }
  return before + after;
}

export function insertSlashToken(value: string, cursor: number): { value: string; cursor: number } {
  if (readSlashQuery(value, cursor)) {
    return { value, cursor };
  }
  const before = value.slice(0, cursor);
  const after = value.slice(cursor);
  const prefix = before.length > 0 && !/\s$/.test(before) ? ' /' : '/';
  return {
    value: `${before}${prefix}${after}`,
    cursor: before.length + prefix.length,
  };
}
