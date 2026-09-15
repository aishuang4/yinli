/** Chat 顶栏上下文占用 selector。 */

import { describe, expect, it } from 'vitest';
import { emptyDisplaySnapshot, type DisplaySnapshot } from '../runtime-stream/protocol';
import {
  describePromptSections,
  resolveContextUsedTokens,
  selectContextUsage,
  selectLatestContextUsage,
  type ContextUsageData,
} from './contextUsage';
import type { LocalMessage } from './types';

function snapshotWithContext(data: ContextUsageData): DisplaySnapshot {
  return {
    version: 4,
    lastSeq: 1,
    streamDone: true,
    order: ['context:run-1'],
    blocksById: {
      'context:run-1': {
        id: 'context:run-1',
        kind: 'context',
        dataVersion: 1,
        phase: 'closed',
        data,
      },
    },
  };
}

describe('selectLatestContextUsage', () => {
  it('优先读 live snapshot，否则回退到最近一条 assistant 消息', () => {
    const live = snapshotWithContext({ inputTokens: 1800, contextWindow: 32768, compressed: 0 });
    const historical: LocalMessage = {
      id: 'a1',
      role: 'assistant',
      content: '旧回复',
      displaySnapshot: snapshotWithContext({ estimatedTokens: 400, compressed: 1 }),
    };

    expect(selectLatestContextUsage(live, [historical])?.inputTokens).toBe(1800);
    expect(selectLatestContextUsage(emptyDisplaySnapshot(), [historical])?.compressed).toBe(1);
    expect(selectContextUsage(emptyDisplaySnapshot())).toBeNull();
  });

  it('system prompt 分段按字符数倒序、最多列出前几段', () => {
    expect(describePromptSections({
      promptSections: { agent: 1200, memory: 3000, runtime: 0, clarification: 400, finalAnswer: 700, project: 500 },
    })).toEqual(['记忆注入 3000 字', 'Agent 提示词 1200 字', '最终回答契约 700 字', '项目上下文 500 字']);
    expect(describePromptSections({})).toEqual([]);
  });

  it('供应商回报 token 优先于字符估算', () => {
    expect(resolveContextUsedTokens({ inputTokens: 1800, estimatedTokens: 900 })).toEqual({
      used: 1800,
      estimated: false,
    });
    expect(resolveContextUsedTokens({ estimatedTokens: 900 })).toEqual({
      used: 900,
      estimated: true,
    });
  });
});
