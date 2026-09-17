/** 订阅工作室独立 SSE。断线用已应用的最大持久 seq 重连，不从 0 重放。 */

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

export interface StudioEvent {
  seq: number | null;
  groupId: string;
  runId: string | null;
  type: string;
  status: string;
  payload: Record<string, unknown>;
}

const BOARD_EVENTS = new Set([
  'GROUP_MEMBER',
  'GROUP_FILE',
  'GROUP_APPROVAL',
  'GROUP_RUN_ENDED',
  'GROUP_BUDGET',
  'GROUP_CIRCUIT',
  'GROUP_ERROR',
  'GROUP_PIN',
  'GROUP_STATUS',
  'GROUP_SPEAKER_QUEUE',
  'GROUP_FLOOR',
]);

/** 历史 V1 事件名不进 BOARD_EVENTS，因此被忽略：GROUP_INTAKE / GROUP_WORK_ITEM / GROUP_DOD / GROUP_BLACKBOARD / GROUP_TASK_CARD */

export function useGroupStudioStream(
  groupId: string | null,
  enabled: boolean,
  onDomainEvent?: (event: StudioEvent) => void,
) {
  const [messages, setMessages] = useState<StudioTimelineMessage[]>([]);
  const [status, setStatus] = useState('');
  const [ready, setReady] = useState(false);
  const domainEventRef = useRef(onDomainEvent);

  useEffect(() => {
    domainEventRef.current = onDomainEvent;
  }, [onDomainEvent]);

  useEffect(() => {
    setMessages([]);
    setStatus('');
    setReady(false);
    if (!groupId || !enabled) {
      return;
    }
    const controller = new AbortController();
    let buffer = '';
    let snapshotStatus = '';
    let lastSeq = 0;
    let reconnectTimer = 0;

    const applyEvent = (event: StudioEvent) => {
      if (typeof event.seq === 'number' && event.seq > lastSeq) {
        lastSeq = event.seq;
      }
      if (event.type === 'GROUP_STUDIO_STATE') {
        const payload = event.payload as { messages?: StudioTimelineMessage[]; status?: string };
        snapshotStatus = String(payload.status || event.status || '');
        setStatus(snapshotStatus);
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
        setReady(true);
        return;
      }
      if (
        snapshotStatus === 'done'
        && (event.type === 'GROUP_ERROR' || event.type === 'GROUP_CIRCUIT')
      ) {
        return;
      }
      if (event.type === 'GROUP_STATUS') {
        const next = String(event.payload.to || event.status || '');
        snapshotStatus = next;
        setStatus(next);
        domainEventRef.current?.(event);
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
              role: String(event.payload.role || 'worker'),
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

    const consume = async (afterSeq: number | null) => {
      const query = afterSeq === null ? '' : `?afterSeq=${afterSeq}`;
      const response = await fetch(
        `/api/group-studio/${encodeURIComponent(groupId)}/events${query}`,
        {
          signal: controller.signal,
          headers: { Accept: 'text/event-stream' },
        },
      );
      if (!response.ok || !response.body) {
        throw new Error('sse_unavailable');
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

    const loop = async () => {
      let first = true;
      while (!controller.signal.aborted) {
        try {
          await consume(first ? null : lastSeq);
          first = false;
        } catch (error: unknown) {
          if (controller.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
            return;
          }
        }
        if (controller.signal.aborted) {
          return;
        }
        await new Promise<void>((resolve) => {
          reconnectTimer = window.setTimeout(resolve, 1200);
        });
      }
    };

    void loop();
    return () => {
      controller.abort();
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
      }
    };
  }, [enabled, groupId]);

  return { messages, status, ready };
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
