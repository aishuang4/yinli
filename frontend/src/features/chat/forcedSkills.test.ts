/** Chat 发送框强制开启 Skill 的选择规则。 */

import { describe, expect, it } from 'vitest';
import type { Skill } from '../../types/skill';
import {
  consumeSlashQuery,
  filterComposerSkills,
  filterSkillsBySlashQuery,
  insertSlashToken,
  matchPlusMenuAction,
  MAX_FORCED_SKILLS,
  readSlashQuery,
  toggleForcedSkillId,
} from './forcedSkills';

function skill(partial: Partial<Skill> & Pick<Skill, 'id'>): Skill {
  return {
    name: partial.id,
    description: '',
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
    ...partial,
  };
}

describe('filterComposerSkills', () => {
  it('Project 没有 Skill 白名单时展示全部已启用项', () => {
    const skills = [skill({ id: 'word-documents' }), skill({ id: 'off', enabled: false })];
    expect(filterComposerSkills(skills, new Set()).map((item) => item.id)).toEqual(['word-documents']);
  });

  it('有 Project binding 时仍保留共享包和项目目录 Skill', () => {
    const skills = [
      skill({ id: 'word-documents', availableToAllAgents: true }),
      skill({ id: 'private-pack', availableToAllAgents: false }),
      skill({ id: 'local-pack', sourceScope: 'project', availableToAllAgents: false }),
    ];
    expect(
      filterComposerSkills(skills, new Set(['word-documents'])).map((item) => item.id),
    ).toEqual(['word-documents', 'local-pack']);
  });
});

describe('toggleForcedSkillId', () => {
  it('选中与取消同一项', () => {
    expect(toggleForcedSkillId([], 'word-documents')).toEqual(['word-documents']);
    expect(toggleForcedSkillId(['word-documents'], 'word-documents')).toEqual([]);
  });

  it(`最多保留 ${MAX_FORCED_SKILLS} 个`, () => {
    const selected = ['a', 'b', 'c'];
    expect(toggleForcedSkillId(selected, 'd')).toEqual(selected);
  });
});

describe('readSlashQuery', () => {
  it('输入开头的 / 会打开命令', () => {
    expect(readSlashQuery('/', 1)).toEqual({ start: 0, end: 1, query: '' });
    expect(readSlashQuery('/word', 5)).toEqual({ start: 0, end: 5, query: 'word' });
  });

  it('空白后的 /token 会打开，Unix 路径不会', () => {
    expect(readSlashQuery('请用 /word', 8)).toEqual({ start: 3, end: 8, query: 'word' });
    expect(readSlashQuery('/Users/foo', 10)).toBeNull();
    expect(readSlashQuery('看看 /Users/foo', 8)).toBeNull();
  });

  it('光标不在斜杠 token 内时不打开', () => {
    expect(readSlashQuery('/word 继续', 8)).toBeNull();
  });
});

describe('filterSkillsBySlashQuery', () => {
  it('按名称、id 或描述过滤', () => {
    const skills = [
      skill({ id: 'word-documents', name: 'word-documents', description: '处理 Word' }),
      skill({ id: 'yinli-charts', name: 'yinli-charts', description: '画图' }),
    ];
    expect(filterSkillsBySlashQuery(skills, 'word').map((item) => item.id)).toEqual(['word-documents']);
    expect(filterSkillsBySlashQuery(skills, '画').map((item) => item.id)).toEqual(['yinli-charts']);
  });
});

describe('matchPlusMenuAction', () => {
  it('空查询展示全部动作', () => {
    expect(matchPlusMenuAction('plan', '')).toBe(true);
    expect(matchPlusMenuAction('image', '图片')).toBe(true);
    expect(matchPlusMenuAction('think', '深度')).toBe(true);
    expect(matchPlusMenuAction('file', '计划')).toBe(false);
  });
});

describe('consumeSlashQuery / insertSlashToken', () => {
  it('选中后吃掉斜杠 token', () => {
    expect(consumeSlashQuery('请用 /word 写报告', { start: 3, end: 8, query: 'word' })).toBe('请用 写报告');
  });

  it('按钮唤起时在光标处插入 /', () => {
    expect(insertSlashToken('你好', 2)).toEqual({ value: '你好 /', cursor: 4 });
    expect(insertSlashToken('/', 1)).toEqual({ value: '/', cursor: 1 });
  });
});
