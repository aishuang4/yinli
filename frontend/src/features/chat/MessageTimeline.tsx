/** 对话左侧时间线。每个用户问题一个点，点击后滚到那条消息。 */

import { useEffect, useMemo, useState, type RefObject } from 'react';
import { Tooltip } from '@/components/design';
import type { LocalMessage } from './types';

export function MessageTimeline({
  messages,
  containerRef,
}: {
  messages: LocalMessage[];
  containerRef: RefObject<HTMLDivElement | null>;
}) {
  const marks = useMemo(() => messages.filter((message) => message.role === 'user'), [messages]);
  const markKey = marks.map((message) => message.id).join('|');
  const [activeId, setActiveId] = useState('');

  useEffect(() => {
    const container = containerRef.current;
    if (!container || marks.length < 2) return undefined;
    const update = () => {
      const edge = container.getBoundingClientRect().top + 28;
      let current = marks[0]?.id || '';
      for (const mark of marks) {
        const node = messageNode(container, mark.id);
        if (!node) continue;
        if (node.getBoundingClientRect().top <= edge) current = mark.id;
      }
      setActiveId(current);
    };
    update();
    container.addEventListener('scroll', update, { passive: true });
    return () => container.removeEventListener('scroll', update);
  }, [containerRef, markKey, marks]);

  if (marks.length < 2) return null;

  return (
    <nav
      aria-label="消息时间线"
      className="pointer-events-none absolute left-1.5 top-1/2 z-10 hidden -translate-y-1/2 lg:block"
    >
      <div className="pointer-events-auto flex max-h-[min(420px,70vh)] flex-col items-center gap-1.5 overflow-y-auto py-1 [scrollbar-width:none]">
        {marks.map((message) => {
          const active = message.id === activeId;
          const label = markLabel(message);
          return (
            <Tooltip key={message.id} title={label} placement="right" mouseEnterDelay={0.2}>
              <button
                type="button"
                aria-label={label}
                aria-current={active ? 'true' : undefined}
                className="group flex h-3.5 w-3.5 shrink-0 items-center justify-center"
                onClick={() => jumpToMessage(containerRef.current, message.id)}
              >
                <span
                  className={[
                    'block rounded-full transition-all',
                    active
                      ? 'h-[7px] w-[7px] bg-[var(--color-text-primary)]'
                      : 'h-[5px] w-[5px] bg-[var(--color-text-tertiary)] opacity-80 group-hover:opacity-100',
                  ].join(' ')}
                />
              </button>
            </Tooltip>
          );
        })}
      </div>
    </nav>
  );
}

function jumpToMessage(container: HTMLDivElement | null, messageId: string) {
  if (!container) return;
  const node = messageNode(container, messageId);
  if (!node) return;
  const top = node.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - 12;
  container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
}

function messageNode(container: HTMLElement, messageId: string): HTMLElement | null {
  return container.querySelector(`[data-chat-message-id="${CSS.escape(messageId)}"]`);
}

function markLabel(message: LocalMessage): string {
  const line = message.content.replace(/\s+/g, ' ').trim();
  if (line) return line.length > 42 ? `${line.slice(0, 41)}…` : line;
  if (message.images?.length) return '图片';
  if (message.files?.length) return message.files[0]?.name || '附件';
  return '消息';
}
