import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { formatProcessDuration } from './chatUiUtils';
import { ProcessTimeline } from './ProcessTimeline';
import type { ChatProcessBlock } from './chatDisplayTypes';

export function ProcessDisplayBlock({
  block,
  defaultExpanded,
  hasFinalAnswer,
}: {
  block: ChatProcessBlock;
  defaultExpanded: boolean;
  hasFinalAnswer: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded && !hasFinalAnswer);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const hadFinalAnswerRef = useRef(hasFinalAnswer);
  const processDuration = formatProcessDuration(block.startedAt, block.completedAt, nowMs);
  const detailCount = block.items.length;
  const hasProcessDetails = detailCount > 0;

  useEffect(() => {
    if (hasProcessDetails && !hasFinalAnswer) {
      setExpanded(true);
      hadFinalAnswerRef.current = hasFinalAnswer;
      return;
    }
    if (!hadFinalAnswerRef.current && hasFinalAnswer) {
      setExpanded(false);
    }
    hadFinalAnswerRef.current = hasFinalAnswer;
  }, [hasFinalAnswer, hasProcessDetails]);

  useEffect(() => {
    if (block.completedAt || !block.startedAt) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [block.completedAt, block.startedAt]);

  if (!hasProcessDetails) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition hover:bg-[var(--color-bg-hover)]"
      >
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium text-[var(--color-text-primary)]">
            {block.completedAt ? '已处理' : '处理中'}
          </span>
          {processDuration && (
            <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
              耗时 {processDuration}
            </span>
          )}
          <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
            {detailCount} 项
          </span>
        </div>
        {expanded ? (
          <ChevronDown size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
        )}
      </button>

      {expanded && (
        <ProcessTimeline
          items={block.items}
          autoScroll={block.status === 'running' || block.status === 'pending'}
        />
      )}
    </div>
  );
}
