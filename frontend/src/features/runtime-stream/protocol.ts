/** Event Stream V3 固定 Frame 与 DisplaySnapshot V4 协议。 */

export type BlockOutcome = 'completed' | 'failed' | 'cancelled';

export interface DisplayBlock<TData = unknown> {
  id: string;
  parent?: string;
  kind: string;
  dataVersion: number;
  phase: 'open' | 'closed';
  outcome?: BlockOutcome;
  data: TData;
}

export interface DisplaySnapshot {
  version: 4;
  lastSeq: number;
  streamDone: boolean;
  order: string[];
  blocksById: Record<string, DisplayBlock>;
}

export interface BlockOpenFrame {
  seq: number;
  op: 'open';
  id: string;
  parent?: string;
  kind: string;
  dataVersion: number;
  patch?: unknown;
}

export interface BlockDeltaFrame {
  seq: number;
  op: 'delta';
  id: string;
  patch: unknown;
}

export interface BlockCloseFrame {
  seq: number;
  op: 'close';
  id: string;
  outcome: BlockOutcome;
  patch?: unknown;
}

export interface SnapshotFrame {
  seq: number;
  snapshot: DisplaySnapshot;
}

export interface DoneFrame {
  seq: number;
  done: true;
}

export type RuntimeFrame =
  | BlockOpenFrame
  | BlockDeltaFrame
  | BlockCloseFrame
  | SnapshotFrame
  | DoneFrame;

export class RuntimeProtocolError extends Error {}

export function emptyDisplaySnapshot(): DisplaySnapshot {
  return { version: 4, lastSeq: 0, streamDone: false, order: [], blocksById: {} };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireInteger(value: unknown, field: string, minimum: number): number {
  if (!Number.isInteger(value) || Number(value) < minimum) {
    throw new RuntimeProtocolError(`${field} 必须为不小于 ${minimum} 的整数`);
  }
  return Number(value);
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new RuntimeProtocolError(`${field} 必须为非空字符串`);
  }
  return value;
}

function assertKeys(value: Record<string, unknown>, allowed: readonly string[]): void {
  const allowedSet = new Set(allowed);
  const unknownKey = Object.keys(value).find((key) => !allowedSet.has(key));
  if (unknownKey) throw new RuntimeProtocolError(`RuntimeFrame 包含未知字段: ${unknownKey}`);
}

function decodeOutcome(value: unknown): BlockOutcome {
  if (value === 'completed' || value === 'failed' || value === 'cancelled') return value;
  throw new RuntimeProtocolError('outcome 非法');
}

export function decodeDisplaySnapshot(value: unknown): DisplaySnapshot {
  if (!isRecord(value) || value.version !== 4) {
    throw new RuntimeProtocolError('DisplaySnapshot version 必须为 4');
  }
  assertKeys(value, ['version', 'lastSeq', 'streamDone', 'order', 'blocksById']);
  const lastSeq = requireInteger(value.lastSeq, 'snapshot.lastSeq', 0);
  if (typeof value.streamDone !== 'boolean' || !Array.isArray(value.order) || !isRecord(value.blocksById)) {
    throw new RuntimeProtocolError('DisplaySnapshot 结构非法');
  }
  const order = value.order.map((id) => requireString(id, 'snapshot.order[]'));
  if (new Set(order).size !== order.length) throw new RuntimeProtocolError('snapshot.order 包含重复 ID');
  if (order.length > 2_000 || order.length > lastSeq) throw new RuntimeProtocolError('Snapshot Block 数量非法');
  const blocksById: Record<string, DisplayBlock> = {};
  for (const [id, rawBlock] of Object.entries(value.blocksById)) {
    if (!isRecord(rawBlock) || rawBlock.id !== id) throw new RuntimeProtocolError(`Block identity 非法: ${id}`);
    assertKeys(rawBlock, ['id', 'parent', 'kind', 'dataVersion', 'phase', 'outcome', 'data']);
    if (!('data' in rawBlock)) throw new RuntimeProtocolError(`Block 缺少 data: ${id}`);
    const phase = rawBlock.phase;
    if (phase !== 'open' && phase !== 'closed') throw new RuntimeProtocolError(`Block phase 非法: ${id}`);
    const outcome = rawBlock.outcome === undefined ? undefined : decodeOutcome(rawBlock.outcome);
    if ((phase === 'open' && outcome !== undefined) || (phase === 'closed' && outcome === undefined)) {
      throw new RuntimeProtocolError(`Block phase/outcome 不一致: ${id}`);
    }
    const parent = rawBlock.parent === undefined ? undefined : requireString(rawBlock.parent, 'block.parent');
    blocksById[id] = {
      id,
      ...(parent ? { parent } : {}),
      kind: requireString(rawBlock.kind, 'block.kind'),
      dataVersion: requireInteger(rawBlock.dataVersion, 'block.dataVersion', 1),
      phase,
      ...(outcome ? { outcome } : {}),
      data: rawBlock.data,
    };
  }
  if (order.some((id) => !(id in blocksById)) || Object.keys(blocksById).length !== order.length) {
    throw new RuntimeProtocolError('snapshot.order 与 blocksById 不一致');
  }
  const positions = new Map(order.map((id, index) => [id, index]));
  for (const id of order) {
    let parent = blocksById[id].parent;
    let depth = 1;
    const seen = new Set([id]);
    while (parent) {
      if (!(parent in blocksById) || Number(positions.get(parent)) >= Number(positions.get(id))) {
        throw new RuntimeProtocolError(`Block parent 顺序非法: ${id}`);
      }
      if (seen.has(parent)) throw new RuntimeProtocolError(`Block parent 出现环: ${id}`);
      seen.add(parent);
      depth += 1;
      if (depth > 16) throw new RuntimeProtocolError(`Block 嵌套深度超过上限: ${id}`);
      parent = blocksById[parent].parent;
    }
  }
  if (value.streamDone && Object.values(blocksById).some((block) => block.phase === 'open')) {
    throw new RuntimeProtocolError('streamDone Snapshot 不能包含 open Block');
  }
  return { version: 4, lastSeq, streamDone: value.streamDone, order, blocksById };
}

export function decodeRuntimeFrame(value: unknown): RuntimeFrame {
  if (!isRecord(value)) throw new RuntimeProtocolError('RuntimeFrame 必须为对象');
  if ('snapshot' in value) {
    assertKeys(value, ['seq', 'snapshot']);
    const snapshot = decodeDisplaySnapshot(value.snapshot);
    const seq = requireInteger(value.seq, 'seq', 0);
    if (seq !== snapshot.lastSeq) throw new RuntimeProtocolError('Snapshot seq 与 lastSeq 不一致');
    return { seq, snapshot };
  }
  const seq = requireInteger(value.seq, 'seq', 1);
  if (value.done === true) {
    assertKeys(value, ['seq', 'done']);
    return { seq, done: true };
  }
  if (value.op === 'open') {
    assertKeys(value, ['seq', 'op', 'id', 'parent', 'kind', 'dataVersion', 'patch']);
    const parent = value.parent === undefined ? undefined : requireString(value.parent, 'parent');
    return {
      seq,
      op: 'open',
      id: requireString(value.id, 'id'),
      ...(parent ? { parent } : {}),
      kind: requireString(value.kind, 'kind'),
      dataVersion: requireInteger(value.dataVersion, 'dataVersion', 1),
      ...('patch' in value ? { patch: value.patch } : {}),
    };
  }
  if (value.op === 'delta') {
    assertKeys(value, ['seq', 'op', 'id', 'patch']);
    if (!('patch' in value)) throw new RuntimeProtocolError('delta 缺少 patch');
    return { seq, op: 'delta', id: requireString(value.id, 'id'), patch: value.patch };
  }
  if (value.op === 'close') {
    assertKeys(value, ['seq', 'op', 'id', 'outcome', 'patch']);
    return {
      seq,
      op: 'close',
      id: requireString(value.id, 'id'),
      outcome: decodeOutcome(value.outcome),
      ...('patch' in value ? { patch: value.patch } : {}),
    };
  }
  throw new RuntimeProtocolError('RuntimeFrame 不是 open/delta/close/snapshot/done');
}
