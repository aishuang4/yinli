/** 只读 Block Tree selector；不创建第二套展示状态。 */

import { snapshotAnswerGeneration } from './answerPartition';
import { coreBlockRegistry } from './coreModules';
import type { DisplayBlock, DisplaySnapshot } from './protocol';

export function hasVisibleRuntimeContent(snapshot: DisplaySnapshot): boolean {
  return snapshot.order.some((id) => {
    const block = snapshot.blocksById[id];
    const presentation = block ? coreBlockRegistry.get(block.kind)?.presentation : undefined;
    return block && presentation !== 'structural' && presentation !== 'detached';
  });
}

export function blocksOfKind<T = unknown>(snapshot: DisplaySnapshot, kind: string): DisplayBlock<T>[] {
  return snapshot.order.flatMap((id) => {
    const block = snapshot.blocksById[id];
    return block?.kind === kind ? [block as DisplayBlock<T>] : [];
  });
}

export function runtimeText(snapshot: DisplaySnapshot): string {
  const answerId = snapshotAnswerGeneration(snapshot);
  const texts = blocksOfKind<string>(snapshot, 'text');
  const selected = answerId ? texts.filter((block) => block.id === answerId) : texts;
  return selected
    .map((block) => typeof block.data === 'string' ? block.data : '')
    .join('');
}

export function runtimeError(snapshot: DisplaySnapshot): string {
  const block = blocksOfKind<Record<string, unknown>>(snapshot, 'error').at(-1);
  return typeof block?.data.message === 'string' ? block.data.message : '';
}
