/** RuntimeFrame 去重、gap buffer、Snapshot 替换和 done 的通用消费适配器。 */

import { applyRuntimeFrame } from './blockTreeReducer';
import {
  decodeRuntimeFrame,
  emptyDisplaySnapshot,
  type DisplaySnapshot,
  type RuntimeFrame,
} from './protocol';
import type { BlockRegistry } from './registry';

export type RuntimeConnection = 'connecting' | 'live' | 'reconnecting' | 'done' | 'protocol_error';

export interface RuntimeStreamState {
  snapshot: DisplaySnapshot;
  gapBuffer: Map<number, RuntimeFrame>;
  gapBytes: number;
  firstGapAt: number | null;
  connection: RuntimeConnection;
  needsSnapshot: boolean;
}

export interface GapLimits {
  maxFrames: number;
  maxBytes: number;
  maxWaitMs: number;
}

const DEFAULT_GAP_LIMITS: GapLimits = { maxFrames: 256, maxBytes: 512 * 1024, maxWaitMs: 5_000 };

export function createRuntimeStreamState(): RuntimeStreamState {
  return {
    snapshot: emptyDisplaySnapshot(),
    gapBuffer: new Map(),
    gapBytes: 0,
    firstGapAt: null,
    connection: 'connecting',
    needsSnapshot: false,
  };
}

function frameBytes(frame: RuntimeFrame): number {
  return new TextEncoder().encode(JSON.stringify(frame)).byteLength;
}

function recoverBySnapshot(state: RuntimeStreamState): RuntimeStreamState {
  return {
    ...state,
    gapBuffer: new Map(),
    gapBytes: 0,
    firstGapAt: null,
    connection: 'reconnecting',
    needsSnapshot: true,
  };
}

export function acceptRuntimeFrame(
  state: RuntimeStreamState,
  input: unknown,
  registry: BlockRegistry,
  options: { now?: number; limits?: GapLimits } = {},
): RuntimeStreamState {
  const now = options.now ?? Date.now();
  const limits = options.limits ?? DEFAULT_GAP_LIMITS;
  const frame = decodeRuntimeFrame(input);
  if ('snapshot' in frame) {
    return {
      snapshot: frame.snapshot,
      gapBuffer: new Map(),
      gapBytes: 0,
      firstGapAt: null,
      connection: frame.snapshot.streamDone ? 'done' : 'live',
      needsSnapshot: false,
    };
  }
  if (frame.seq <= state.snapshot.lastSeq) return state;
  if (frame.seq > state.snapshot.lastSeq + 1) {
    if (state.gapBuffer.has(frame.seq)) return state;
    const gapBuffer = new Map(state.gapBuffer);
    gapBuffer.set(frame.seq, frame);
    const gapBytes = state.gapBytes + frameBytes(frame);
    const firstGapAt = state.firstGapAt ?? now;
    const buffered = { ...state, gapBuffer, gapBytes, firstGapAt, connection: 'reconnecting' as const };
    if (
      gapBuffer.size > limits.maxFrames
      || gapBytes > limits.maxBytes
      || now - firstGapAt > limits.maxWaitMs
    ) return recoverBySnapshot(buffered);
    return buffered;
  }

  let snapshot = applyRuntimeFrame(state.snapshot, frame, registry);
  const gapBuffer = new Map(state.gapBuffer);
  let gapBytes = state.gapBytes;
  while (gapBuffer.has(snapshot.lastSeq + 1)) {
    const next = gapBuffer.get(snapshot.lastSeq + 1)!;
    gapBuffer.delete(next.seq);
    gapBytes -= frameBytes(next);
    snapshot = applyRuntimeFrame(snapshot, next, registry);
  }
  return {
    snapshot,
    gapBuffer,
    gapBytes: Math.max(0, gapBytes),
    firstGapAt: gapBuffer.size ? state.firstGapAt : null,
    connection: snapshot.streamDone ? 'done' : 'live',
    needsSnapshot: false,
  };
}

export function checkRuntimeGapTimeout(
  state: RuntimeStreamState,
  now = Date.now(),
  limits: GapLimits = DEFAULT_GAP_LIMITS,
): RuntimeStreamState {
  if (state.firstGapAt === null || now - state.firstGapAt <= limits.maxWaitMs) return state;
  return recoverBySnapshot(state);
}
