/** 只读 Block Tree selector；不创建第二套展示状态。 */

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
  return blocksOfKind<string>(snapshot, 'text')
    .map((block) => typeof block.data === 'string' ? block.data : '')
    .join('');
}

export function runtimeError(snapshot: DisplaySnapshot): string {
  const block = blocksOfKind<Record<string, unknown>>(snapshot, 'error').at(-1);
  return typeof block?.data.message === 'string' ? block.data.message : '';
}
