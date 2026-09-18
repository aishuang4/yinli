/** Skill 列表键盘高亮滚动。 */

import { describe, expect, it } from 'vitest';
import { buildPlusMenuCategories, buildPlusMenuItems } from './plusMenu';
import { alignSkillHighlightInView } from './ChatSkillPicker';
import type { Skill } from '../../types/skill';

function box(top: number, bottom: number, scrollTop = 0) {
  return {
    scrollTop,
    getBoundingClientRect: () => ({ top, bottom }),
  };
}

describe('alignSkillHighlightInView', () => {
  it('高亮在可视区下方时向下滚', () => {
    const container = box(0, 100, 0);
    alignSkillHighlightInView(container, box(80, 140));
    expect(container.scrollTop).toBe(40);
  });

  it('高亮在可视区上方时向上滚', () => {
    const container = box(0, 100, 80);
    alignSkillHighlightInView(container, box(-20, 20));
    expect(container.scrollTop).toBe(60);
  });

  it('高亮已在可视区内时不滚动', () => {
    const container = box(0, 100, 12);
    alignSkillHighlightInView(container, box(20, 80));
    expect(container.scrollTop).toBe(12);
  });
});

describe('buildPlusMenuItems', () => {
  const skill: Skill = {
    id: 'word-documents',
    name: 'word-documents',
    description: '处理 Word',
    category: 'test',
    trigger: '',
    content: '',
    tags: [],
    resourcePath: '',
    resources: [],
    status: 'installed',
    enabled: true,
    installedAt: null,
    updatedAt: '2026-09-18T00:00:00Z',
  };
  const agents = [
    { id: 'default', name: '默认助手', avatar: '', description: '通用助手' },
    { id: 'dev', name: '软件开发团队', avatar: '', description: '写代码' },
  ];
  const base = {
    showPlan: true,
    showTeam: true,
    skillsEnabled: true,
    skills: [skill],
    agents,
  };

  it('Skills 关闭时不展示技能分类', () => {
    expect(buildPlusMenuCategories({ skillsEnabled: true })).toEqual(['files', 'modes', 'agents', 'skills']);
    expect(buildPlusMenuCategories({ skillsEnabled: false })).toEqual(['files', 'modes', 'agents']);
  });

  it('按分类拆成文件、模式、Agent、技能', () => {
    expect(buildPlusMenuItems({ ...base, category: 'files', query: '' }).map((item) => item.kind)).toEqual([
      'image',
      'file',
    ]);
    expect(buildPlusMenuItems({ ...base, category: 'modes', query: '' }).map((item) => item.kind)).toEqual([
      'plan',
      'think',
      'agent-mode',
      'team-mode',
    ]);
    expect(buildPlusMenuItems({ ...base, category: 'agents', query: '' }).map((item) => (
      item.kind === 'agent' ? item.agent.id : item.kind
    ))).toEqual(['default', 'dev']);
    expect(buildPlusMenuItems({ ...base, category: 'skills', query: '' }).map((item) => (
      item.kind === 'skill' ? item.skill.id : item.kind
    ))).toEqual(['word-documents']);
  });

  it('按搜索词只保留当前分类的匹配项', () => {
    expect(buildPlusMenuItems({
      ...base,
      category: 'modes',
      query: '深度',
    }).map((item) => item.kind)).toEqual(['think']);
    expect(buildPlusMenuItems({
      ...base,
      category: 'skills',
      query: 'word',
    }).map((item) => (item.kind === 'skill' ? item.skill.id : item.kind))).toEqual(['word-documents']);
    expect(buildPlusMenuItems({
      ...base,
      category: 'agents',
      query: '开发',
    }).map((item) => (item.kind === 'agent' ? item.agent.id : item.kind))).toEqual(['dev']);
  });
});
