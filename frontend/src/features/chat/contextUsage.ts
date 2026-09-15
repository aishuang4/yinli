/** 从 DisplaySnapshot 读取当前会话上下文占用。 */

import { blocksOfKind } from '../runtime-stream/selectors';
import type { DisplaySnapshot } from '../runtime-stream/protocol';
import type { LocalMessage } from './types';

export type ContextUsageData = {
  systemPromptChars?: number;
  summaryChars?: number;
  contextChars?: number;
  contextMessages?: number;
  toolResultBlocks?: number;
  toolCount?: number;
  contextWindow?: number;
  triggerRatio?: number;
  triggerTokens?: number;
  estimatedTokens?: number;
  compressed?: number;
  overTrigger?: number;
  inputTokens?: number;
  outputTokens?: number;
};

export function selectContextUsage(snapshot?: DisplaySnapshot | null): ContextUsageData | null {
  if (!snapshot) {
    return null;
  }
  const block = blocksOfKind<ContextUsageData>(snapshot, 'context').at(-1);
  if (!block || !block.data || typeof block.data !== 'object') {
    return null;
  }
  return block.data;
}

export function selectLatestContextUsage(
  live: DisplaySnapshot,
  messages: LocalMessage[],
): ContextUsageData | null {
  return selectContextUsage(live)
    || [...messages].reverse().map((item) => selectContextUsage(item.displaySnapshot)).find(Boolean)
    || null;
}

export function resolveContextUsedTokens(usage: ContextUsageData): { used: number; estimated: boolean } {
  const actual = Number(usage.inputTokens || 0);
  if (actual > 0) {
    return { used: actual, estimated: false };
  }
  return { used: Number(usage.estimatedTokens || 0), estimated: true };
}
