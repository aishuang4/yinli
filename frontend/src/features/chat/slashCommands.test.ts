/** Chat 斜杠指令过滤。 */

import { describe, expect, it } from 'vitest';
import { buildSlashList, commandMatchesQuery, visibleSlashCommands } from './slashCommands';
import type { Skill } from '../../types/skill';

function skill(id: string): Skill {
  return {
    id,
    name: id,
    description: `${id} skill`,
    category: 'test',
    trigger: '',
    content: '',
    tags: [],
    resourcePath: '',
    resources: [],
    status: 'installed',
    enabled: true,
    installedAt: null,
    updatedAt: '2026-09-20T00:00:00Z',
  };
}

describe('slash commands', () => {
  it('空查询时指令在 Skill 前面，压缩始终可见', () => {
    const items = buildSlashList({
      query: '',
      skills: [skill('word-documents')],
      planEnabled: true,
      teamEnabled: true,
    });
    expect(items[0]).toMatchObject({ kind: 'command', command: { id: 'compress' } });
    expect(items.some((item) => item.kind === 'skill' && item.skill.id === 'word-documents')).toBe(true);
  });

  it('输入 压缩 只留下压缩指令', () => {
    const items = buildSlashList({
      query: '压缩',
      skills: [skill('word-documents')],
      planEnabled: true,
      teamEnabled: false,
    });
    expect(items).toEqual([
      expect.objectContaining({ kind: 'command', command: expect.objectContaining({ id: 'compress' }) }),
    ]);
  });

  it('关掉 Plan / Team 时不展示对应指令', () => {
    const ids = visibleSlashCommands({ planEnabled: false, teamEnabled: false }).map((item) => item.id);
    expect(ids).toEqual(['compress', 'think']);
  });

  it('compress 别名能匹配', () => {
    expect(commandMatchesQuery(visibleSlashCommands({ planEnabled: true, teamEnabled: true })[0], 'compress')).toBe(true);
  });
});
