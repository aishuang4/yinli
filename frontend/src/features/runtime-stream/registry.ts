/** Block kind 的数据 decoder、纯 reducer 与 renderer 注册表。 */

import type { ComponentType } from 'react';
import type { DisplayBlock } from './protocol';

export type BlockPresentation = 'structural' | 'process' | 'answer' | 'supplement' | 'detached';

export interface BlockModule<TData = unknown, TPatch = unknown> {
  kind: string;
  displayName: string;
  presentation: BlockPresentation;
  currentDataVersion: number;
  emptyInitial: () => TData;
  decodeInitial: (dataVersion: number, value: unknown) => TData;
  decodePatch: (dataVersion: number, value: unknown) => TPatch;
  reduce: (data: TData, patch: TPatch) => TData;
  renderer: ComponentType<{ block: DisplayBlock<TData> }>;
}

export type UnknownBlockModule = BlockModule<unknown, unknown>;

export class BlockRegistry {
  private readonly modules = new Map<string, UnknownBlockModule>();

  register<TData, TPatch>(module: BlockModule<TData, TPatch>): void {
    if (this.modules.has(module.kind)) throw new Error(`Block kind 重复注册: ${module.kind}`);
    this.modules.set(module.kind, module as UnknownBlockModule);
  }

  get(kind: string): UnknownBlockModule | undefined {
    return this.modules.get(kind);
  }
}

export function requireVersion(actual: number, expected: number, kind: string): void {
  if (actual !== expected) throw new Error(`${kind} dataVersion 不支持: ${actual}`);
}

export function decodeString(value: unknown, field: string): string {
  if (typeof value !== 'string') throw new Error(`${field} 必须为字符串`);
  return value;
}

export function decodeRecord(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`${field} 必须为对象`);
  }
  return { ...value as Record<string, unknown> };
}
