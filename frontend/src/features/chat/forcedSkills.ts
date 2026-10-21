/** Chat 发送框强制开启 Skill 的纯函数。 */

import type { Skill } from '../../types/skill';

export const MAX_FORCED_SKILLS = 5;

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

export type SkillTextSegment = {
  text: string;
  skill: boolean;
};

function skillKey(value: string): string {
  return value.trim().toLowerCase();
}

/** 斜杠名對上 Skill 的 name 或 id。同名只留先出現的那一個。 */
export function skillBySlashToken(skills: Skill[]): Map<string, Skill> {
  const lookup = new Map<string, Skill>();
  for (const skill of skills) {
    for (const key of [skill.name, skill.id]) {
      const normalized = skillKey(key);
      if (normalized && !lookup.has(normalized)) lookup.set(normalized, skill);
    }
  }
  return lookup;
}

const INLINE_SKILL_PATTERN = /(^|\s)(\/([^\s/]+))/g;

/** 按出現順序取出最多 max 個斜杠 Skill。第 6 個起不再算選中。 */
export function inlineSkillIds(value: string, skills: Skill[], max = MAX_FORCED_SKILLS): string[] {
  const lookup = skillBySlashToken(skills);
  const ids: string[] = [];
  const seen = new Set<string>();
  for (const match of value.matchAll(INLINE_SKILL_PATTERN)) {
    const skill = lookup.get(skillKey(match[3] || ''));
    if (!skill || seen.has(skill.id)) continue;
    if (ids.length >= max) break;
    seen.add(skill.id);
    ids.push(skill.id);
  }
  return ids;
}

/** 把文字切成普通段和會被後端認領的斜杠 Skill。 */
export function skillTextSegments(value: string, skills: Skill[], max = MAX_FORCED_SKILLS): SkillTextSegment[] {
  const lookup = skillBySlashToken(skills);
  const seen = new Set<string>();
  let loaded = 0;
  const parts: SkillTextSegment[] = [];
  let cursor = 0;
  for (const match of value.matchAll(INLINE_SKILL_PATTERN)) {
    const lead = match[1] || '';
    const token = match[2] || '';
    const start = (match.index ?? 0) + lead.length;
    const skill = lookup.get(skillKey(match[3] || ''));
    const take = Boolean(skill) && !seen.has(skill!.id) && loaded < max;
    if (start > cursor) parts.push({ text: value.slice(cursor, start), skill: false });
    if (take && skill) {
      seen.add(skill.id);
      loaded += 1;
      parts.push({ text: token, skill: true });
    } else {
      parts.push({ text: token, skill: false });
    }
    cursor = start + token.length;
  }
  if (cursor < value.length) parts.push({ text: value.slice(cursor), skill: false });
  if (parts.length === 0) parts.push({ text: value, skill: false });
  return parts;
}

/** 用選中的 Skill 名換掉正在輸入的斜杠片段，並在後面留一個空格。 */
export function completeSlashSkill(value: string, slash: SlashQuery, name: string): { value: string; cursor: number } {
  const after = value.slice(slash.end);
  const needsSpace = !/^\s/.test(after);
  const token = `/${name}${needsSpace ? ' ' : ''}`;
  const skippedSpace = needsSpace ? 0 : (after.match(/^\s*/)?.[0].length ?? 0);
  return {
    value: value.slice(0, slash.start) + token + after,
    cursor: slash.start + token.length + skippedSpace,
  };
}

/** 在游標處插入 `/name`。游標正停在斜杠片段上時，改成補全該片段。 */
export function insertSkillToken(value: string, cursor: number, name: string): { value: string; cursor: number } {
  const slash = readSlashQuery(value, cursor);
  if (slash) return completeSlashSkill(value, slash, name);
  const pos = Math.max(0, Math.min(cursor, value.length));
  const before = value.slice(0, pos);
  const after = value.slice(pos);
  const lead = before.length > 0 && !/\s$/.test(before) ? ' ' : '';
  const trail = after.length === 0 || !/^\s/.test(after) ? ' ' : '';
  const token = `${lead}/${name}${trail}`;
  return {
    value: before + token + after,
    cursor: before.length + token.length,
  };
}

/** 從原文拿掉第一次出現的 `/name` 或 `/id`。 */
export function removeInlineSkill(value: string, skill: Pick<Skill, 'id' | 'name'>): string {
  for (const key of [skill.name, skill.id]) {
    const trimmed = key.trim();
    if (!trimmed) continue;
    const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pattern = new RegExp(`(^|\\s)/${escaped}(?=\\s|$)`, 'i');
    if (!pattern.test(value)) continue;
    return value.replace(pattern, '$1').replace(/[ \t]{2,}/g, ' ').replace(/^[ \t]+/, '');
  }
  return value;
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
