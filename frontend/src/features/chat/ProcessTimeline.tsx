import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { BookOpenCheck, Brain, ChevronDown, ChevronRight, Lightbulb, Wrench } from 'lucide-react';
import { Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import type { SkillCallInfo } from '../../hooks/useEventStream';
import { ToolCallBlock } from './ToolArtifacts';
import type { ChatProcessItem } from './chatDisplayTypes';

export function ProcessTimeline({
  items,
  autoScroll = false,
}: {
  items: ChatProcessItem[];
  autoScroll?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const scrollSignature = useMemo(
    () => items.map((item) => `${item.id}:${item.status}:${item.updatedAt}:${processItemContentLength(item)}`).join('|'),
    [items],
  );

  useLayoutEffect(() => {
    if (!autoScroll || !shouldStickToBottomRef.current) return;
    const node = scrollRef.current;
    if (!node) return;

    node.scrollTop = node.scrollHeight;
    const frame = window.requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [autoScroll, scrollSignature]);

  const handleScroll = () => {
    const node = scrollRef.current;
    if (!node) return;
    const distanceToBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
    shouldStickToBottomRef.current = distanceToBottom < 48;
  };

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      className="max-h-[400px] overflow-y-auto border-t border-[var(--color-border)] px-3 py-3 pr-2"
    >
      <div className="space-y-4">
        {items.map((item) => (
          <ProcessTimelineItem key={`${item.type}-${item.id}`} item={item} />
        ))}
      </div>
    </div>
  );
}

function processItemContentLength(item: ChatProcessItem): number {
  if (item.type === 'reasoning' || item.type === 'thinking') {
    return item.content.length;
  }
  if (item.type === 'tool') {
    return `${item.toolCall.args}${item.toolCall.result}${item.toolCall.status}`.length;
  }
  if (item.type === 'skill') {
    return `${item.skillCall.content}${item.skillCall.status}`.length;
  }
  return 0;
}

function ProcessTimelineItem({ item }: { item: ChatProcessItem }) {
  if (item.type === 'reasoning') {
    return <TextStep icon={<Brain size={15} />} content={item.content} />;
  }
  if (item.type === 'thinking') {
    return <TextStep icon={<Lightbulb size={15} />} content={item.content} />;
  }
  if (item.type === 'tool') {
    return (
      <ActionShell icon={<Wrench size={15} />} label={`Action · ${item.toolCall.name}`}>
        <ToolCallBlock toolCall={item.toolCall} />
      </ActionShell>
    );
  }
  if (item.type === 'skill') {
    return (
      <ActionShell icon={<BookOpenCheck size={15} />} label={`Skill · ${item.skillCall.name}`}>
        <SkillCallBlock skillCall={item.skillCall} />
      </ActionShell>
    );
  }
  return null;
}

function TextStep({ icon, content }: { icon: ReactNode; content: string }) {
  if (!content.trim()) return null;
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)]">
        {icon}
      </div>
      <div className="flex min-h-6 min-w-0 flex-1 items-center text-sm leading-7 text-[var(--color-text-primary)]">
        <MarkdownContent content={content} className="w-full" />
      </div>
    </div>
  );
}

function ActionShell({
  icon,
  label,
  children,
}: {
  icon: ReactNode;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex gap-3">
      <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)]">
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <h6 className="mb-1 text-xs font-semibold leading-5 text-[var(--color-text-tertiary)]">
          {label}
        </h6>
        {children}
      </div>
    </div>
  );
}

function SkillCallBlock({ skillCall }: { skillCall: SkillCallInfo }) {
  const [expanded, setExpanded] = useState(false);
  const statusText = skillCall.status === 'running'
    ? '使用中'
    : skillCall.status === 'failed'
      ? '失败'
      : '已使用';
  const statusColor = skillCall.status === 'running'
    ? 'blue'
    : skillCall.status === 'failed'
      ? 'danger'
      : 'success';

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition hover:bg-[var(--color-bg-hover)]"
      >
        <div className="flex min-w-0 items-center gap-2">
          <BookOpenCheck size={14} color="var(--color-success)" />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {skillCall.name}
            </div>
            <div className="truncate text-xs text-[var(--color-text-tertiary)]">
              {skillCall.trigger || skillCall.description || '已注入 Skill 操作手册'}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Tag size="small" color={statusColor}>
            {statusText}
          </Tag>
          {expanded ? (
            <ChevronDown size={14} className="text-[var(--color-text-tertiary)]" />
          ) : (
            <ChevronRight size={14} className="text-[var(--color-text-tertiary)]" />
          )}
        </div>
      </button>
      {expanded && (
        <div className="space-y-2 px-3 pb-3">
          {skillCall.content && (
            <div className="max-h-72 overflow-auto rounded-md bg-[var(--color-bg-primary)] p-3 text-xs leading-6 text-[var(--color-text-secondary)]">
              <MarkdownContent content={skillCall.content} />
            </div>
          )}
          {skillCall.resources.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {skillCall.resources.slice(0, 8).map((resource) => (
                <Tag key={resource} size="small" color="transparent">
                  {resource}
                </Tag>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
