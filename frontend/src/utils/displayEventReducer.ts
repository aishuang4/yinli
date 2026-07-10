import {
  DISPLAY_BLOCK_ORDER,
  DISPLAY_BLOCKS,
  DISPLAY_EVENT_OPS,
  DISPLAY_EVENTS,
  DISPLAY_SNAPSHOT_VERSION,
  type DisplayBlock,
  type DisplayActor,
  type DisplayData,
  type DisplayEvent,
  type DisplayItem,
  type DisplaySnapshot,
} from '../types/displayEvents';

export function createEmptyDisplaySnapshot(): DisplaySnapshot {
  return {
    version: DISPLAY_SNAPSHOT_VERSION,
    mode: 'single_agent',
    actors: [],
    blocks: [],
  };
}

export function normalizeDisplaySnapshot(snapshot: DisplaySnapshot | null | undefined): DisplaySnapshot {
  if (!snapshot || !Array.isArray(snapshot.blocks)) {
    return createEmptyDisplaySnapshot();
  }

  return {
    version: DISPLAY_SNAPSHOT_VERSION,
    mode: 'single_agent',
    actors: normalizeDisplayActors(snapshot.actors, snapshot.blocks),
    blocks: sortDisplayBlocks(snapshot.blocks.flatMap(normalizeDisplayBlock)),
  };
}

export function applyDisplayEvent(snapshot: DisplaySnapshot, event: DisplayEvent | null | undefined): DisplaySnapshot {
  if (!event) return normalizeDisplaySnapshot(snapshot);
  const current = normalizeDisplaySnapshot(snapshot);
  const blockId = resolveBlockId(event);

  if (event.block === DISPLAY_BLOCKS.PROCESS && event.actorId) {
    return applyActorProcessEvent(current, event);
  }

  if (event.op === DISPLAY_EVENT_OPS.REMOVE) {
    return {
      ...current,
      blocks: removeDisplayTarget(current.blocks, event, blockId),
    };
  }

  if (event.op === DISPLAY_EVENT_OPS.CLEAR) {
    return {
      ...current,
      blocks: current.blocks.map((block) => (
        block.id === blockId ? clearDisplayBlock(block, event.createdAt) : block
      )),
    };
  }

  const existingBlock = current.blocks.find((block) => block.id === blockId);
  const nextBlock = applyDisplayEventToBlock(existingBlock, event, blockId);
  const blocks = existingBlock
    ? current.blocks.map((block) => (block.id === blockId ? nextBlock : block))
    : [...current.blocks, nextBlock];

  return {
    ...current,
    blocks: sortDisplayBlocks(blocks),
  };
}

function normalizeDisplayActors(actors: unknown, blocks?: DisplayBlock[]): DisplayActor[] {
  if (Array.isArray(actors) && actors.length > 0) {
    return actors.flatMap(normalizeDisplayActor);
  }

  const processBlock = Array.isArray(blocks)
    ? blocks.find((block) => block?.block === DISPLAY_BLOCKS.PROCESS)
    : undefined;
  if (!processBlock || !Array.isArray(processBlock.items) || processBlock.items.length === 0) {
    return [];
  }
  return normalizeDisplayActor({
    id: 'single-agent',
    role: 'single',
    status: processBlock.status || 'running',
    updatedAt: processBlock.updatedAt || '',
    items: processBlock.items,
  });
}

function normalizeDisplayActor(actor: unknown): DisplayActor[] {
  if (!actor || typeof actor !== 'object') return [];
  const record = actor as Partial<DisplayActor> & Record<string, unknown>;
  const id = String(record.id || record.agentId || '');
  if (!id) return [];
  return [{
    id,
    role: 'single',
    agentId: String(record.agentId || ''),
    agentName: String(record.agentName || 'Agent'),
    status: String(record.status || 'running'),
    task: String(record.task || ''),
    detail: String(record.detail || ''),
    output: String(record.output || ''),
    createdAt: String(record.createdAt || record.updatedAt || ''),
    updatedAt: String(record.updatedAt || record.createdAt || ''),
    completedAt: String(record.completedAt || ''),
    items: Array.isArray(record.items) ? record.items.flatMap(normalizeDisplayItem) : [],
    data: normalizeDisplayData(record.data),
  }];
}

function applyActorProcessEvent(snapshot: DisplaySnapshot, event: DisplayEvent): DisplaySnapshot {
  const actorId = String(event.actorId || '');
  if (!actorId) return snapshot;
  const existingActor = snapshot.actors.find((actor) => actor.id === actorId);
  const actor = applyProcessEventToActor(existingActor || buildActorFromProcessEvent(event), event);
  const actors = existingActor
    ? snapshot.actors.map((current) => (current.id === actorId ? actor : current))
    : [...snapshot.actors, actor];
  return {
    ...snapshot,
    mode: 'single_agent',
    actors,
  };
}

function buildActorFromProcessEvent(event: DisplayEvent): DisplayActor {
  const actorId = String(event.actorId || 'single-agent');
  return {
    id: actorId,
    role: 'single',
    agentId: String(event.data?.agentId || ''),
    agentName: String(event.data?.agentName || 'Agent'),
    status: event.status || 'running',
    task: '',
    detail: '',
    output: '',
    createdAt: event.createdAt,
    updatedAt: event.createdAt,
    completedAt: '',
    items: [],
    data: {},
  };
}

function applyProcessEventToActor(actor: DisplayActor, event: DisplayEvent): DisplayActor {
  const block: DisplayBlock = {
    id: actor.id,
    block: DISPLAY_BLOCKS.PROCESS,
    status: actor.status,
    updatedAt: actor.updatedAt,
    items: actor.items,
    data: actor.data,
  };
  const nextBlock = applyProcessEvent(block, event);
  return {
    ...actor,
    status: event.status || actor.status,
    updatedAt: event.createdAt || actor.updatedAt,
    items: nextBlock.items,
  };
}

function normalizeDisplayBlock(block: DisplayBlock | null | undefined): DisplayBlock[] {
  if (!block || !block.id || !block.block) return [];
  return [{
    id: String(block.id),
    block: block.block,
    status: String(block.status || 'running'),
    updatedAt: String(block.updatedAt || ''),
    items: Array.isArray(block.items) ? block.items.flatMap(normalizeDisplayItem) : [],
    data: normalizeDisplayData(block.data),
  }];
}

function normalizeDisplayItem(item: DisplayItem | null | undefined): DisplayItem[] {
  if (!item || !item.id) return [];
  const updatedAt = String(item.updatedAt || item.createdAt || '');
  return [{
    id: String(item.id),
    event: String(item.event || ''),
    targetId: String(item.targetId || item.id),
    status: item.status ? String(item.status) : undefined,
    createdAt: item.createdAt ? String(item.createdAt) : updatedAt,
    updatedAt,
    data: normalizeDisplayData(item.data),
  }];
}

function normalizeDisplayData(data: unknown): DisplayData {
  return data && typeof data === 'object' && !Array.isArray(data)
    ? { ...(data as DisplayData) }
    : {};
}

function resolveBlockId(event: DisplayEvent): string {
  if (event.block === DISPLAY_BLOCKS.PLAN) {
    const planId = event.data?.planId || event.targetId;
    return `plan-${String(planId || 'latest')}`;
  }
  return event.block;
}

function removeDisplayTarget(blocks: DisplayBlock[], event: DisplayEvent, blockId: string): DisplayBlock[] {
  if (!event.targetId || event.block !== DISPLAY_BLOCKS.PROCESS) {
    return blocks.filter((block) => block.id !== blockId);
  }

  return blocks.flatMap((block) => {
    if (block.id !== blockId) return [block];
    const items = block.items.filter((item) => item.targetId !== event.targetId && item.id !== event.targetId);
    return items.length > 0 || Object.keys(block.data).length > 0
      ? [{ ...block, items, updatedAt: event.createdAt }]
      : [];
  });
}

function clearDisplayBlock(block: DisplayBlock, updatedAt: string): DisplayBlock {
  return {
    ...block,
    items: [],
    data: {},
    updatedAt,
  };
}

function applyDisplayEventToBlock(
  existingBlock: DisplayBlock | undefined,
  event: DisplayEvent,
  blockId: string,
): DisplayBlock {
  const baseBlock: DisplayBlock = existingBlock || {
    id: blockId,
    block: event.block,
    status: event.status || 'running',
    updatedAt: event.createdAt,
    items: [],
    data: {},
  };

  if (event.block === DISPLAY_BLOCKS.PROCESS) {
    return applyProcessEvent(baseBlock, event);
  }

  return {
    ...baseBlock,
    status: event.status || baseBlock.status,
    updatedAt: event.createdAt || baseBlock.updatedAt,
    data: applyBlockData(baseBlock.data, event),
  };
}

function applyProcessEvent(block: DisplayBlock, event: DisplayEvent): DisplayBlock {
  const item = buildProcessItem(event);
  const existingIndex = block.items.findIndex((current) => (
    current.id === item.id || current.targetId === item.targetId
  ));
  const items = [...block.items];

  if (existingIndex === -1) {
    items.push(item);
  } else {
    items[existingIndex] = mergeDisplayItem(items[existingIndex], item, event);
  }

  return {
    ...block,
    status: event.status || block.status,
    updatedAt: event.createdAt || block.updatedAt,
    items,
  };
}

function buildProcessItem(event: DisplayEvent): DisplayItem {
  const targetId = event.targetId || defaultProcessTargetId(event.event);
  return {
    id: targetId,
    event: event.event,
    targetId,
    status: event.status,
    createdAt: event.createdAt,
    updatedAt: event.createdAt,
    data: normalizeDisplayData(event.data),
  };
}

function defaultProcessTargetId(eventName: string): string {
  if (eventName === DISPLAY_EVENTS.THINKING_DELTA) return 'thinking';
  if (eventName.startsWith('tool.')) return 'tool';
  if (eventName.startsWith('skill.')) return 'skill';
  return 'reasoning';
}

function mergeDisplayItem(current: DisplayItem, incoming: DisplayItem, event: DisplayEvent): DisplayItem {
  if (event.op === DISPLAY_EVENT_OPS.REPLACE) {
    return incoming;
  }

  if (event.op === DISPLAY_EVENT_OPS.APPEND) {
    return {
      ...current,
      event: incoming.event || current.event,
      status: incoming.status || current.status,
      updatedAt: incoming.updatedAt || current.updatedAt,
      data: appendItemData(current.data, incoming.data, event.event),
    };
  }

  return {
    ...current,
    event: incoming.event || current.event,
    status: incoming.status || current.status,
    updatedAt: incoming.updatedAt || current.updatedAt,
    data: mergeItemData(current.data, incoming.data),
  };
}

function mergeItemData(current: DisplayData, incoming: DisplayData): DisplayData {
  const merged = {
    ...current,
    ...incoming,
  };
  for (const key of ['inputPreview', 'outputPreview', 'error']) {
    if (String(incoming[key] ?? '') === '' && String(current[key] ?? '') !== '') {
      merged[key] = current[key];
    }
  }
  return merged;
}

function applyBlockData(current: DisplayData, event: DisplayEvent): DisplayData {
  const incoming = normalizeDisplayData(event.data);
  if (event.op === DISPLAY_EVENT_OPS.REPLACE) {
    return incoming;
  }
  if (event.op === DISPLAY_EVENT_OPS.APPEND) {
    if (event.event === DISPLAY_EVENTS.ANSWER_DELTA) {
      return appendTextData(current, incoming, 'content');
    }
    return appendTextData(current, incoming, 'text');
  }
  return {
    ...current,
    ...incoming,
  };
}

function appendItemData(current: DisplayData, incoming: DisplayData, eventName: string): DisplayData {
  if (eventName === DISPLAY_EVENTS.TOOL_INPUT_DELTA) {
    return appendTextData(current, incoming, 'inputPreview');
  }
  if (eventName === DISPLAY_EVENTS.TOOL_OUTPUT_DELTA) {
    return appendTextData(current, incoming, 'outputPreview');
  }
  if (eventName === DISPLAY_EVENTS.REASONING_DELTA || eventName === DISPLAY_EVENTS.THINKING_DELTA) {
    return appendTextData(current, incoming, 'text');
  }
  return appendTextData(current, incoming, 'content');
}

function appendTextData(current: DisplayData, incoming: DisplayData, preferredKey: string): DisplayData {
  const incomingText = String(
    incoming[preferredKey] ??
    incoming.text ??
    incoming.delta ??
    incoming.content ??
    '',
  );
  const currentText = String(current[preferredKey] ?? current.text ?? current.content ?? '');
  return {
    ...current,
    ...incoming,
    [preferredKey]: `${currentText}${incomingText}`,
  };
}

function sortDisplayBlocks(blocks: DisplayBlock[]): DisplayBlock[] {
  return [...blocks].sort((a, b) => {
    const left = DISPLAY_BLOCK_ORDER.indexOf(a.block);
    const right = DISPLAY_BLOCK_ORDER.indexOf(b.block);
    const safeLeft = left === -1 ? DISPLAY_BLOCK_ORDER.length : left;
    const safeRight = right === -1 ? DISPLAY_BLOCK_ORDER.length : right;
    if (safeLeft !== safeRight) return safeLeft - safeRight;
    return a.id.localeCompare(b.id);
  });
}
