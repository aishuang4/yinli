/** 历史消息中 AskUser/clarification 过程项的合并与恢复。 */

import { isAskUserToolName } from './humanInteractionUtils';
import type { LocalMessage } from './types';
import { DISPLAY_EVENTS, type DisplayItem } from '../../types/displayEvents';

function pickDisplayItemString(item: DisplayItem, keys: string[]): string {
  for (const key of keys) {
    const value = item.data[key];
    if (value === undefined || value === null) continue;
    const text = String(value);
    if (text) return text;
  }
  return '';
}

export function mergeResolvedClarificationFeedbackIntoReplies(messages: LocalMessage[]): LocalMessage[] {
  const merged: LocalMessage[] = [];
  let pendingClarification: DisplayItem | null = null;

  for (const message of messages) {
    if (message.role !== 'assistant') {
      merged.push(message);
      continue;
    }
    const storedAskUserItem = askUserToolItemFromMessage(message);
    if (storedAskUserItem && !message.content.trim()) {
      if (hasToolOutput(storedAskUserItem)) {
        pendingClarification = storedAskUserItem;
        continue;
      }
      merged.push(message);
      continue;
    }
    const clarificationItem = processItemFromResolvedClarificationMessage(message);
    if (clarificationItem && isWaitingClarificationMessage(message)) continue;
    if (clarificationItem && !message.content.trim()) {
      if (hasToolOutput(clarificationItem)) pendingClarification = clarificationItem;
      continue;
    }
    if (pendingClarification) {
      merged.push({
        ...message,
        displaySnapshot: prependProcessItemToMessageSnapshot(message, pendingClarification),
      });
      pendingClarification = null;
      continue;
    }
    merged.push(message);
  }

  if (pendingClarification) {
    merged.push({
      id: `assistant-clarification-${pendingClarification.id}`,
      role: 'assistant',
      content: '',
      createdAt: pendingClarification.createdAt,
      displaySnapshot: snapshotFromProcessItem(pendingClarification, 'waiting'),
    });
  }
  return merged;
}

function askUserToolItemFromMessage(message: LocalMessage): DisplayItem | null {
  const items = (message.displaySnapshot?.actors || [])
    .flatMap((actor) => Array.isArray(actor.items) ? actor.items : []);
  return items.find((item) => (
    item.event.startsWith('tool.') &&
    isAskUserToolName(pickDisplayItemString(item, ['name', 'toolName', 'rawName']))
  )) || null;
}

function hasToolOutput(item: DisplayItem): boolean {
  return Boolean(pickDisplayItemString(item, ['outputPreview', 'output', 'result', 'error']).trim());
}

function isWaitingClarificationMessage(message: LocalMessage): boolean {
  const feedbackBlock = message.displaySnapshot?.blocks?.find((block) => block.block === 'feedback');
  const approval = feedbackBlock?.data?.approval;
  if (!approval || typeof approval !== 'object') return false;
  const status = String((approval as Record<string, unknown>).status || feedbackBlock.status || '');
  return status === 'waiting' && !message.clarificationResolved;
}

function processItemFromResolvedClarificationMessage(message: LocalMessage): DisplayItem | null {
  const feedbackBlock = message.displaySnapshot?.blocks?.find((block) => block.block === 'feedback');
  const approval = feedbackBlock?.data?.approval;
  if (!approval || typeof approval !== 'object') return null;
  const record = approval as Record<string, unknown>;
  if (String(record.kind || record.type || '') !== 'clarification') return null;

  const questions = Array.isArray(record.questions) ? record.questions : [];
  const inputPreview = [
    `模型反问：${String(record.title || '需要补充信息').trim()}`,
    String(record.description || '').trim() ? `说明：${String(record.description || '').trim()}` : '',
    ...questions.flatMap((question, index) => {
      if (!question || typeof question !== 'object') return [];
      const label = String((question as Record<string, unknown>).label || '').trim();
      return label ? [`${index + 1}. ${label}`] : [];
    }),
  ].filter(Boolean).join('\n');
  const outputPreview = extractClarificationAnswerFromMessage(message, record);
  const interactionId = String(record.interactionId || message.id || 'clarification');
  const createdAt = String(record.createdAt || message.createdAt || '');

  return {
    id: `clarification-${interactionId}`,
    event: outputPreview ? DISPLAY_EVENTS.TOOL_COMPLETED : DISPLAY_EVENTS.TOOL_STARTED,
    targetId: `clarification-${interactionId}`,
    status: outputPreview ? 'completed' : 'waiting',
    createdAt,
    updatedAt: message.createdAt || createdAt,
    data: {
      name: 'AskUser', inputPreview, outputPreview, outputSize: outputPreview.length,
      outputTruncated: false, error: '',
    },
  };
}

function extractClarificationAnswerFromMessage(message: LocalMessage, approval: Record<string, unknown>): string {
  const preview = approval.externalResultsPreview;
  if (preview && typeof preview === 'object' && !Array.isArray(preview)) {
    const values = Object.values(preview as Record<string, unknown>)
      .map((value) => String(value || '').trim()).filter(Boolean);
    if (values.length) return values.join('\n');
  }
  const text = String(message.content || '');
  if (!text.includes('针对上一个补充信息请求，用户回答如下')) return '';
  const answerLine = [...text.split('\n')].map((line) => line.trim()).reverse()
    .find((line) => line.startsWith('- ') && !line.startsWith('- 原始请求:') && line.includes(':'));
  if (!answerLine) return '';
  const [, answer = ''] = answerLine.split(/:(.*)/s);
  return answer.trim();
}

function prependProcessItemToMessageSnapshot(message: LocalMessage, item: DisplayItem) {
  const snapshot = message.displaySnapshot;
  const resolvedItem = resolveClarificationProcessItem(item);
  if (!snapshot) return snapshotFromProcessItem(resolvedItem, message.error ? 'failed' : 'completed');
  const actors = Array.isArray(snapshot.actors) ? [...snapshot.actors] : [];
  if (actors.length === 0) {
    return { ...snapshot, actors: snapshotFromProcessItem(resolvedItem, message.error ? 'failed' : 'completed').actors };
  }
  const firstActor = actors[0];
  const existingItems = Array.isArray(firstActor.items) ? firstActor.items : [];
  if (existingItems.some((existing) => existing.id === resolvedItem.id || existing.targetId === resolvedItem.targetId)) {
    return snapshot;
  }
  actors[0] = { ...firstActor, items: [resolvedItem, ...existingItems] };
  return { ...snapshot, actors };
}

function resolveClarificationProcessItem(item: DisplayItem): DisplayItem {
  const inputPreview = pickDisplayItemString(item, ['inputPreview', 'input', 'args']);
  const storedOutput = pickDisplayItemString(item, ['outputPreview', 'output', 'result', 'error']);
  return {
    ...item,
    event: storedOutput ? DISPLAY_EVENTS.TOOL_COMPLETED : item.event,
    status: storedOutput ? 'completed' : item.status,
    data: {
      ...item.data, inputPreview, input: inputPreview, outputPreview: storedOutput,
      output: storedOutput, outputSize: storedOutput.length, outputTruncated: false, error: '',
    },
  };
}

function snapshotFromProcessItem(item: DisplayItem, status: string) {
  return {
    version: 3 as const,
    mode: 'single_agent' as const,
    actors: [{
      id: 'single-agent', role: 'single' as const, agentId: '', agentName: 'Agent', status,
      task: '', detail: '', output: '', createdAt: item.createdAt || '', updatedAt: item.updatedAt,
      completedAt: '', items: [item], data: {},
    }],
    blocks: [],
  };
}
