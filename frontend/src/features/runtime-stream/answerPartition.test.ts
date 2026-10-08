/** 终态折叠只在 answerGeneration 或旧的已完成正文上发生。 */

import { describe, expect, it, vi } from 'vitest';
import { partitionMessageChildren, snapshotAnswerGeneration } from './answerPartition';
import { emptyDisplaySnapshot, type DisplayBlock, type DisplaySnapshot } from './protocol';
import { runtimeText } from './selectors';

vi.mock('./coreModules', () => {
  const presentationByKind: Record<string, string> = {
    message: 'structural',
    text: 'answer',
    reasoning: 'process',
    thinking: 'process',
    tool: 'process',
    plan: 'detached',
    artifact: 'supplement',
    context: 'detached',
    error: 'supplement',
  };
  return {
    coreBlockRegistry: {
      get(kind: string) {
        const presentation = presentationByKind[kind] ?? 'process';
        return { presentation };
      },
    },
  };
});

function block(partial: {
  id: string;
  kind: string;
  parent?: string;
  phase?: DisplayBlock['phase'];
  outcome?: DisplayBlock['outcome'];
  data?: unknown;
}): DisplayBlock {
  const phase = partial.phase ?? 'closed';
  return {
    id: partial.id,
    kind: partial.kind,
    parent: partial.parent,
    dataVersion: 1,
    phase,
    outcome: phase === 'open' ? undefined : partial.outcome ?? 'completed',
    data: partial.data ?? (partial.kind === 'text' ? '' : {}),
  };
}

function snapshot(blocks: DisplayBlock[]): DisplaySnapshot {
  const next = emptyDisplaySnapshot();
  next.order = blocks.map((item) => item.id);
  next.blocksById = Object.fromEntries(blocks.map((item) => [item.id, item]));
  return next;
}

describe('partitionMessageChildren', () => {
  it('终态信号出现前按时间平铺，正文也不进折叠', () => {
    const tree = snapshot([
      block({ id: 'message:run', kind: 'message', phase: 'open', data: { role: 'assistant' } }),
      block({ id: 'text:run:1', kind: 'text', parent: 'message:run', phase: 'open', data: '先看一下' }),
      block({ id: 'tool:read', kind: 'tool', parent: 'message:run', phase: 'open', data: { name: 'Read' } }),
    ]);
    expect(partitionMessageChildren(tree, 'message:run', ['text:run:1', 'tool:read'])).toEqual({
      mode: 'flat',
      processIds: [],
      answerIds: [],
      supplementIds: [],
    });
  });

  it('answerGeneration 指向的正文留在外面，其余收进过程', () => {
    const tree = snapshot([
      block({
        id: 'message:run',
        kind: 'message',
        data: { answerGeneration: 'text:run:2' },
      }),
      block({ id: 'text:run:1', kind: 'text', parent: 'message:run', data: '先看一下' }),
      block({ id: 'tool:read', kind: 'tool', parent: 'message:run', data: { name: 'Read' } }),
      block({ id: 'thinking:run:1', kind: 'thinking', parent: 'message:run', data: '想一下' }),
      block({ id: 'text:run:2', kind: 'text', parent: 'message:run', data: '这是结果' }),
      block({ id: 'artifact:run', kind: 'artifact', parent: 'message:run', data: { path: 'a.md' } }),
      block({ id: 'plan:run', kind: 'plan', parent: 'message:run', data: { title: '计划' } }),
    ]);
    expect(partitionMessageChildren(tree, 'message:run', [
      'text:run:1',
      'tool:read',
      'thinking:run:1',
      'text:run:2',
      'artifact:run',
      'plan:run',
    ])).toEqual({
      mode: 'folded',
      processIds: ['text:run:1', 'tool:read', 'thinking:run:1'],
      answerIds: ['text:run:2'],
      supplementIds: ['artifact:run'],
    });
  });

  it('只有最终正文时不产生过程项', () => {
    const tree = snapshot([
      block({ id: 'message:run', kind: 'message', data: { answerGeneration: 'text:run:1' } }),
      block({ id: 'text:run:1', kind: 'text', parent: 'message:run', data: '直接回答' }),
    ]);
    expect(partitionMessageChildren(tree, 'message:run', ['text:run:1'])).toEqual({
      mode: 'folded',
      processIds: [],
      answerIds: ['text:run:1'],
      supplementIds: [],
    });
  });

  it('旧的已完成快照把全部正文当作最终回答', () => {
    const tree = snapshot([
      block({ id: 'message:run', kind: 'message', data: { role: 'assistant' } }),
      block({ id: 'reasoning:run', kind: 'reasoning', parent: 'message:run', data: '过程' }),
      block({ id: 'tool:read', kind: 'tool', parent: 'message:run', data: { name: 'Read' } }),
      block({ id: 'text:run', kind: 'text', parent: 'message:run', data: '当年的答案' }),
    ]);
    expect(partitionMessageChildren(tree, 'message:run', ['reasoning:run', 'tool:read', 'text:run'])).toEqual({
      mode: 'folded',
      processIds: ['reasoning:run', 'tool:read'],
      answerIds: ['text:run'],
      supplementIds: [],
    });
  });

  it('取消或失败且没有终态信号时保持平铺', () => {
    const cancelled = snapshot([
      block({ id: 'message:run', kind: 'message', outcome: 'cancelled', data: {} }),
      block({ id: 'text:run', kind: 'text', parent: 'message:run', outcome: 'cancelled', data: '说到一半' }),
      block({ id: 'tool:read', kind: 'tool', parent: 'message:run', outcome: 'cancelled', data: { name: 'Read' } }),
    ]);
    expect(partitionMessageChildren(cancelled, 'message:run', ['text:run', 'tool:read']).mode).toBe('flat');
    const failed = snapshot([
      block({ id: 'message:run', kind: 'message', outcome: 'failed', data: {} }),
      block({ id: 'text:run', kind: 'text', parent: 'message:run', data: '没写完' }),
    ]);
    expect(partitionMessageChildren(failed, 'message:run', ['text:run']).mode).toBe('flat');
  });
});

describe('runtimeText', () => {
  it('有终态信号时只取对应正文，否则拼接全部正文', () => {
    const live = snapshot([
      block({ id: 'message:run', kind: 'message', phase: 'open', data: {} }),
      block({ id: 'text:run:1', kind: 'text', parent: 'message:run', data: '过程' }),
      block({ id: 'text:run:2', kind: 'text', parent: 'message:run', data: '答案' }),
    ]);
    expect(runtimeText(live)).toBe('过程答案');
    const done = snapshot([
      block({
        id: 'message:run',
        kind: 'message',
        data: { answerGeneration: 'text:run:2' },
      }),
      block({ id: 'text:run:1', kind: 'text', parent: 'message:run', data: '过程' }),
      block({ id: 'text:run:2', kind: 'text', parent: 'message:run', data: '答案' }),
    ]);
    expect(runtimeText(done)).toBe('答案');
    expect(snapshotAnswerGeneration(done)).toBe('text:run:2');
  });
});
