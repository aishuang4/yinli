/** 终态信号出现后，才区分折叠过程和留在外面的最终回答。 */

import { coreBlockRegistry } from './coreModules';
import type { DisplayBlock, DisplaySnapshot } from './protocol';

export type AnswerPartitionMode = 'flat' | 'folded';

export interface AnswerPartition {
  mode: AnswerPartitionMode;
  processIds: string[];
  answerIds: string[];
  supplementIds: string[];
}

function presentationOf(block: DisplayBlock): string {
  return coreBlockRegistry.get(block.kind)?.presentation || 'process';
}

export function messageAnswerGeneration(block: DisplayBlock | undefined): string {
  const data = block?.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return '';
  const value = (data as Record<string, unknown>).answerGeneration;
  return typeof value === 'string' ? value : '';
}

/** 同一快照里后出现的消息字段覆盖先前的值。 */
export function snapshotAnswerGeneration(snapshot: DisplaySnapshot): string {
  let found = '';
  for (const id of snapshot.order) {
    const block = snapshot.blocksById[id];
    if (block?.kind !== 'message') continue;
    const value = messageAnswerGeneration(block);
    if (value) found = value;
  }
  return found;
}

function isLegacyCompletedAnswer(message: DisplayBlock, textIds: string[]): boolean {
  return message.phase === 'closed'
    && message.outcome === 'completed'
    && textIds.length > 0;
}

export function partitionMessageChildren(
  snapshot: DisplaySnapshot,
  messageId: string,
  childIds: string[],
): AnswerPartition {
  const message = snapshot.blocksById[messageId];
  const visible = childIds.filter((id) => {
    const child = snapshot.blocksById[id];
    return Boolean(child && presentationOf(child) !== 'detached');
  });
  const answerGeneration = message ? messageAnswerGeneration(message) : '';
  const textIds = visible.filter((id) => snapshot.blocksById[id]?.kind === 'text');
  const legacy = Boolean(message && !answerGeneration && isLegacyCompletedAnswer(message, textIds));
  if (!answerGeneration && !legacy) {
    return { mode: 'flat', processIds: [], answerIds: [], supplementIds: [] };
  }
  const answerSet = new Set(answerGeneration ? textIds.filter((id) => id === answerGeneration) : textIds);
  const processIds: string[] = [];
  const answerIds: string[] = [];
  const supplementIds: string[] = [];
  for (const id of visible) {
    const child = snapshot.blocksById[id];
    if (!child) continue;
    if (presentationOf(child) === 'supplement') {
      supplementIds.push(id);
      continue;
    }
    if (answerSet.has(id)) {
      answerIds.push(id);
      continue;
    }
    processIds.push(id);
  }
  return { mode: 'folded', processIds, answerIds, supplementIds };
}
