/** 从 DisplaySnapshot V4 派生 Team Console 只读模型。 */

import type { DisplayBlock, DisplaySnapshot } from './protocol';

export type TeamRecord = Record<string, unknown>;
export type TeamRecordBlock = DisplayBlock<TeamRecord>;

function isRecordData(value: unknown): value is TeamRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asRecordBlock(block: DisplayBlock): TeamRecordBlock | null {
  return isRecordData(block.data) ? block as TeamRecordBlock : null;
}

export function isBlockDescendant(
  snapshot: DisplaySnapshot,
  blockId: string,
  ancestorId: string,
): boolean {
  let parent = snapshot.blocksById[blockId]?.parent;
  while (parent) {
    if (parent === ancestorId) return true;
    parent = snapshot.blocksById[parent]?.parent;
  }
  return false;
}

export function selectTeamBlocks(
  snapshot: DisplaySnapshot,
  teamBlockId: string,
  kind: string,
): TeamRecordBlock[] {
  return snapshot.order.flatMap((id) => {
    const block = snapshot.blocksById[id];
    if (!block || block.kind !== kind || !isBlockDescendant(snapshot, id, teamBlockId)) return [];
    const record = asRecordBlock(block);
    return record ? [record] : [];
  });
}

export function selectTaskActivityBlocks(
  snapshot: DisplaySnapshot,
  taskBlockId: string,
): DisplayBlock[] {
  return snapshot.order.flatMap((id) => {
    const block = snapshot.blocksById[id];
    if (!block || !isBlockDescendant(snapshot, id, taskBlockId)) return [];
    return block.kind === 'team-activity' || block.kind === 'tool' ? [block] : [];
  });
}

export function selectTeamRoot(snapshot: DisplaySnapshot, teamBlockId: string): TeamRecordBlock | null {
  const block = snapshot.blocksById[teamBlockId];
  return block && block.kind === 'team' ? asRecordBlock(block) : null;
}
