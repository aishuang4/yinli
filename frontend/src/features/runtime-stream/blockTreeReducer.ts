/** 只处理 identity、parent、生命周期和 kind 分发的通用 Block Tree reducer。 */

import type {
  BlockCloseFrame,
  BlockDeltaFrame,
  BlockOpenFrame,
  DisplayBlock,
  DisplaySnapshot,
  RuntimeFrame,
} from './protocol';
import type { BlockRegistry } from './registry';

export class BlockTreeError extends Error {}

function cloneSnapshot(snapshot: DisplaySnapshot): DisplaySnapshot {
  return {
    ...snapshot,
    order: [...snapshot.order],
    blocksById: Object.fromEntries(
      Object.entries(snapshot.blocksById).map(([id, block]) => [
        id,
        { ...block, data: structuredClone(block.data) },
      ]),
    ),
  };
}

function openBlock(snapshot: DisplaySnapshot, frame: BlockOpenFrame, registry: BlockRegistry): void {
  if (snapshot.streamDone) throw new BlockTreeError('done 后不能 open Block');
  if (snapshot.blocksById[frame.id]) throw new BlockTreeError(`Block 重复 open: ${frame.id}`);
  if (frame.parent) {
    const parent = snapshot.blocksById[frame.parent];
    if (!parent || parent.phase !== 'open') throw new BlockTreeError(`parent 不可用: ${frame.parent}`);
    let depth = 2;
    let ancestor = parent.parent;
    while (ancestor) {
      depth += 1;
      if (depth > 16) throw new BlockTreeError('Block 嵌套深度超过上限');
      ancestor = snapshot.blocksById[ancestor]?.parent;
    }
  }
  if (snapshot.order.length >= 2_000) throw new BlockTreeError('Block 数量超过上限');
  const module = registry.get(frame.kind);
  const data = module
    ? ('patch' in frame
        ? module.decodeInitial(frame.dataVersion, frame.patch)
        : module.emptyInitial())
    : {};
  snapshot.blocksById[frame.id] = {
    id: frame.id,
    ...(frame.parent ? { parent: frame.parent } : {}),
    kind: frame.kind,
    dataVersion: frame.dataVersion,
    phase: 'open',
    data,
  };
  snapshot.order.push(frame.id);
}

function requireOpenBlock(snapshot: DisplaySnapshot, id: string): DisplayBlock {
  const block = snapshot.blocksById[id];
  if (!block) throw new BlockTreeError(`Block 不存在: ${id}`);
  if (block.phase !== 'open') throw new BlockTreeError(`Block 已关闭: ${id}`);
  return block;
}

function reducePatch(
  block: DisplayBlock,
  patch: unknown,
  registry: BlockRegistry,
): unknown {
  const module = registry.get(block.kind);
  if (!module) return block.data;
  const decoded = module.decodePatch(block.dataVersion, patch);
  return module.reduce(block.data, decoded);
}

function deltaBlock(snapshot: DisplaySnapshot, frame: BlockDeltaFrame, registry: BlockRegistry): void {
  const block = requireOpenBlock(snapshot, frame.id);
  block.data = reducePatch(block, frame.patch, registry);
}

function hasOpenDescendant(snapshot: DisplaySnapshot, id: string): boolean {
  return snapshot.order.some((candidateId) => {
    const candidate = snapshot.blocksById[candidateId];
    if (candidate.phase !== 'open' || candidate.id === id) return false;
    let parent = candidate.parent;
    while (parent) {
      if (parent === id) return true;
      parent = snapshot.blocksById[parent]?.parent;
    }
    return false;
  });
}

function closeBlock(snapshot: DisplaySnapshot, frame: BlockCloseFrame, registry: BlockRegistry): void {
  const block = requireOpenBlock(snapshot, frame.id);
  if (hasOpenDescendant(snapshot, frame.id)) {
    throw new BlockTreeError(`Block 仍有 open descendant: ${frame.id}`);
  }
  if ('patch' in frame) block.data = reducePatch(block, frame.patch, registry);
  block.phase = 'closed';
  block.outcome = frame.outcome;
}

function finishStream(snapshot: DisplaySnapshot): void {
  if (snapshot.streamDone) throw new BlockTreeError('stream 已 done');
  if (Object.values(snapshot.blocksById).some((block) => block.phase === 'open')) {
    throw new BlockTreeError('仍有 open Block，不能 done');
  }
  snapshot.streamDone = true;
}

export function applyRuntimeFrame(
  snapshot: DisplaySnapshot,
  frame: RuntimeFrame,
  registry: BlockRegistry,
): DisplaySnapshot {
  if ('snapshot' in frame) return frame.snapshot;
  if (frame.seq !== snapshot.lastSeq + 1) {
    throw new BlockTreeError(`Frame seq 不连续: expected=${snapshot.lastSeq + 1}, actual=${frame.seq}`);
  }
  const next = cloneSnapshot(snapshot);
  if ('done' in frame) finishStream(next);
  else if (frame.op === 'open') openBlock(next, frame, registry);
  else if (frame.op === 'delta') deltaBlock(next, frame, registry);
  else closeBlock(next, frame, registry);
  next.lastSeq = frame.seq;
  return next;
}
