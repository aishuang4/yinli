/** 把同一 message 的 ReAct 过程 Block 统一收进一个可折叠区域。 */

import { useEffect, useRef, useState } from 'react';
import { AlertCircle, ChevronRight, Loader2 } from 'lucide-react';
import type { DisplayBlock } from './protocol';

export function RuntimeProcessGroup({
  blocks,
  hasFinalAnswer,
  children,
}: {
  blocks: DisplayBlock[];
  hasFinalAnswer: boolean;
  children: React.ReactNode;
}) {
  const hasOpenBlock = blocks.some((block) => block.phase === 'open');
  const hasFailedBlock = blocks.some((block) => block.outcome === 'failed');
  const [expanded, setExpanded] = useState(hasOpenBlock || !hasFinalAnswer);
  const previousOpenRef = useRef(hasOpenBlock);
  const previousFinalAnswerRef = useRef(hasFinalAnswer);

  useEffect(() => {
    if (!previousOpenRef.current && hasOpenBlock) {
      setExpanded(true);
    } else if (!previousFinalAnswerRef.current && hasFinalAnswer && !hasOpenBlock) {
      setExpanded(false);
    }
    previousOpenRef.current = hasOpenBlock;
    previousFinalAnswerRef.current = hasFinalAnswer;
  }, [hasFinalAnswer, hasOpenBlock]);

  return (
    <div className="rounded-md bg-[var(--runtime-process-surface)] p-1 text-sm [--runtime-process-surface:color-mix(in_srgb,var(--color-bg-secondary)_68%,transparent)]">
      <button
        type="button"
        aria-expanded={expanded}
        className="group flex min-h-9 w-full cursor-pointer items-center gap-2 rounded-md border-0 bg-transparent px-2 py-1.5 text-left text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)]"
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--color-text-tertiary)] transition-colors group-hover:text-[var(--color-text-secondary)]">
          {hasOpenBlock ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <ChevronRight
              size={14}
              className={`transition-transform ${expanded ? 'rotate-90' : ''}`}
            />
          )}
        </span>
        <span className="min-w-0 flex-1 truncate">
          {hasOpenBlock ? '正在执行' : '已完成'}
        </span>
        {hasFailedBlock ? (
          <span className="inline-flex items-center gap-1 text-[var(--color-error)]">
            <AlertCircle size={12} />
            包含失败
          </span>
        ) : null}
        <span className="rounded bg-[var(--color-bg-primary)] px-1.5 py-0.5 font-normal tabular-nums text-[var(--color-text-tertiary)]">
          {blocks.length} 项
        </span>
      </button>
      {expanded ? (
        <div className="relative mx-2 mb-1 mt-0.5 space-y-0.5 pl-6">
          {children}
        </div>
      ) : null}
    </div>
  );
}
