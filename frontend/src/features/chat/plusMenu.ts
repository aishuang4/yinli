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

export const PLUS_MENU_LEFT_WIDTH = 188;
export const PLUS_MENU_RIGHT_WIDTH = 300;
export const PLUS_MENU_GAP = 6;
export const PLUS_MENU_VIEWPORT_PAD = 8;
export const PLUS_MENU_CATEGORY_HOVER_DELAY_MS = 120;

export type PlusMenuPrimaryPosition = {
  left: number;
  rightLeft: number;
  primaryBottom: number;
};

export type PlusMenuFlyoutPosition = PlusMenuPrimaryPosition & {
  submenuTop: number;
};

/**
 * 一级面板贴发送按钮向上展开，并决定二级的水平位置。
 * 必须先用这组坐标把一级摆正，再量一级项去算二级 top，否则会量到兜底位置。
 */
export function computePlusMenuPrimaryPosition({
  anchorLeft,
  anchorTop,
  viewportWidth,
  viewportHeight,
  leftWidth = PLUS_MENU_LEFT_WIDTH,
  rightWidth = PLUS_MENU_RIGHT_WIDTH,
  gap = PLUS_MENU_GAP,
  padding = PLUS_MENU_VIEWPORT_PAD,
}: {
  anchorLeft: number;
  anchorTop: number;
  viewportWidth: number;
  viewportHeight: number;
  leftWidth?: number;
  rightWidth?: number;
  gap?: number;
  padding?: number;
}): PlusMenuPrimaryPosition {
  let left = anchorLeft;
  if (left + leftWidth > viewportWidth - padding) {
    left = viewportWidth - padding - leftWidth;
  }
  left = Math.max(padding, left);
  let rightLeft = left + leftWidth + gap;
  if (rightLeft + rightWidth > viewportWidth - padding) {
    rightLeft = left - gap - rightWidth;
  }
  rightLeft = Math.max(padding, rightLeft);
  return {
    left,
    rightLeft,
    primaryBottom: viewportHeight - anchorTop + padding,
  };
}

/** 二级顶部跟随当前一级项，再钳到视口内，避免短面板贴在一级底部。 */
export function computePlusMenuSubmenuTop({
  categoryItemTop,
  submenuHeight,
  viewportHeight,
  padding = PLUS_MENU_VIEWPORT_PAD,
}: {
  categoryItemTop: number;
  submenuHeight: number;
  viewportHeight: number;
  padding?: number;
}): number {
  const maxTop = Math.max(padding, viewportHeight - submenuHeight - padding);
  return Math.min(Math.max(categoryItemTop, padding), maxTop);
}

/**
 * 计算 + 菜单双栏坐标：一级贴发送按钮向上展开，二级跟随当前一级项顶部，
 * 再钳到视口内，避免短面板与一级共用 bottom 贴在底部、横移时误触其他分类。
 */
export function computePlusMenuFlyoutPosition({
  anchorLeft,
  anchorTop,
  categoryItemTop,
  submenuHeight,
  viewportWidth,
  viewportHeight,
  leftWidth = PLUS_MENU_LEFT_WIDTH,
  rightWidth = PLUS_MENU_RIGHT_WIDTH,
  gap = PLUS_MENU_GAP,
  padding = PLUS_MENU_VIEWPORT_PAD,
}: {
  anchorLeft: number;
  anchorTop: number;
  categoryItemTop: number;
  submenuHeight: number;
  viewportWidth: number;
  viewportHeight: number;
  leftWidth?: number;
  rightWidth?: number;
  gap?: number;
  padding?: number;
}): PlusMenuFlyoutPosition {
  return {
    ...computePlusMenuPrimaryPosition({
      anchorLeft,
      anchorTop,
      viewportWidth,
      viewportHeight,
      leftWidth,
      rightWidth,
      gap,
      padding,
    }),
    submenuTop: computePlusMenuSubmenuTop({
      categoryItemTop,
      submenuHeight,
      viewportHeight,
      padding,
    }),
  };
}

export function samePlusMenuPosition(
  current: PlusMenuFlyoutPosition | null,
  next: PlusMenuFlyoutPosition,
): boolean {
  return Boolean(
    current
    && current.left === next.left
    && current.rightLeft === next.rightLeft
    && current.primaryBottom === next.primaryBottom
    && current.submenuTop === next.submenuTop,
  );
}

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
