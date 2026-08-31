/** 订阅工作室独立 SSE，把 MESSAGE 事件折成时间线。 */

import { useEffect, useRef, useState } from 'react';

export interface StudioTimelineMessage {
  id: string;
  speakerId: string;
  role: string;
  intent: string;
  body: string;
  thinking?: string;
  visibleInTimeline: boolean;
  streaming?: boolean;
  thinkingStreaming?: boolean;
}

interface StudioEvent {
  seq: number | null;
  groupId: string;
  runId: string | null;
  type: string;
  status: string;
  payload: Record<string, unknown>;
}

const BOARD_EVENTS = new Set([
  'GROUP_BLACKBOARD',
  'GROUP_MEMBER',
  'GROUP_FILE',
  'GROUP_APPROVAL',
  'GROUP_RUN_ENDED',
  'GROUP_BUDGET',
  'GROUP_CIRCUIT',
  'GROUP_DOD',
  'GROUP_ERROR',
  'GROUP_TASK_CARD',
  'GROUP_STATUS',
  'GROUP_INTAKE',
  'GROUP_WORK_ITEM',
  'GROUP_SPEAKER_QUEUE',
]);

export function useGroupStudioStream(
  groupId: string | null,
  enabled: boolean,
  onDomainEvent?: (event: StudioEvent) => void,
) {
  const [messages, setMessages] = useState<StudioTimelineMessage[]>([]);
  const [status, setStatus] = useState('');
  const domainEventRef = useRef(onDomainEvent);
  domainEventRef.current = onDomainEvent;

  useEffect(() => {
    setMessages([]);
    setStatus('');
    if (!groupId || !enabled) {
      return;
    }
    const controller = new AbortController();
    let buffer = '';

    const applyEvent = (event: StudioEvent) => {
      if (event.type === 'GROUP_STUDIO_STATE') {
        const payload = event.payload as { messages?: StudioTimelineMessage[]; status?: string };
        setStatus(String(payload.status || event.status || ''));
        setMessages(
          (payload.messages || [])
            .filter((item) => item.visibleInTimeline !== false)
            .map((item) => ({
              ...item,
              thinking: item.thinking || '',
              streaming: false,
              thinkingStreaming: false,
            })),
        );
        return;
      }
      if (event.type === 'GROUP_STATUS') {
        setStatus(String(event.payload.to || event.status || ''));
        return;
      }
      if (event.type === 'GROUP_MESSAGE_OPEN') {
        const messageId = String(event.payload.messageId || '');
        setMessages((current) => {
          if (current.some((item) => item.id === messageId)) {
            return current;
          }
          return [
            ...current,
            {
              id: messageId,
              speakerId: String(event.payload.speakerId || ''),
              role: String(event.payload.role || 'host'),
              intent: String(event.payload.intent || 'discuss'),
              body: '',
              thinking: '',
              visibleInTimeline: event.payload.visibleInTimeline !== false,
              streaming: true,
              thinkingStreaming: false,
            },
          ];
        });
        return;
      }
      if (event.type === 'GROUP_MESSAGE_DELTA') {
        const messageId = String(event.payload.messageId || '');
        const text = String(event.payload.text || '');
        const kind = String(event.payload.kind || 'answer');
        setMessages((current) => current.map((item) => {
          if (item.id !== messageId) {
            return item;
          }
          if (kind === 'thinking') {
            return {
              ...item,
              thinking: `${item.thinking || ''}${text}`,
              streaming: true,
              thinkingStreaming: true,
            };
          }
          return {
            ...item,
            body: item.body + text,
            streaming: true,
            thinkingStreaming: false,
          };
        }));
        return;
      }
      if (event.type === 'GROUP_MESSAGE_CLOSE') {
        const messageId = String(event.payload.messageId || '');
        const body = String(event.payload.body || '');
        const thinking = String(event.payload.thinking || '');
        setMessages((current) => current.map((item) => (
          item.id === messageId
            ? {
              ...item,
              body,
              thinking: thinking || item.thinking || '',
              streaming: false,
              thinkingStreaming: false,
            }
            : item
        )));
        return;
      }
      if (BOARD_EVENTS.has(event.type)) {
        domainEventRef.current?.(event);
      }
    };

    const consume = async () => {
      const response = await fetch(`/api/group-studio/${encodeURIComponent(groupId)}/events`, {
        signal: controller.signal,
        headers: { Accept: 'text/event-stream' },
      });
      if (!response.ok || !response.body) {
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const block of parts) {
          const parsed = parseSseBlock(block);
          if (parsed) {
            applyEvent(parsed);
          }
        }
      }
    };

    void consume().catch((error: unknown) => {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
    });
    return () => controller.abort();
  }, [enabled, groupId]);

  return { messages, status };
}

function parseSseBlock(block: string): StudioEvent | null {
  let eventType = '';
  const dataLines: string[] = [];
  for (const line of block.split('\n')) {
    if (line.startsWith(':')) {
      continue;
    }
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim();
      continue;
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  try {
    const payload = JSON.parse(dataLines.join('\n')) as StudioEvent;
    if (!payload.type && eventType) {
      payload.type = eventType;
    }
    return payload;
  } catch {
    return null;
  }
}
