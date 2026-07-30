/**
 * TraceToolList — 运行轨迹中的工具调用列表。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { UIEvent } from 'react';
import { Copy, Wrench } from 'lucide-react';
import { Tag } from '@/components/design';
import type { ToolCallInfo } from '../../hooks/useEventStream';
import {
  copyTextToClipboard,
  formatLongTextPreview,
  normalizeToolDisplayName,
  parseHttpToolSummary,
  TRACE_RENDER_BATCH_SIZE,
} from './traceUtils';

export function TraceToolList({
  title,
  toolCalls,
  compact = false,
}: {
  title: string;
  toolCalls: ToolCallInfo[];
  compact?: boolean;
}) {
  const [visibleCount, setVisibleCount] = useState(TRACE_RENDER_BATCH_SIZE);
  const visibleToolCalls = useMemo(
    () => toolCalls.slice(0, visibleCount),
    [toolCalls, visibleCount],
  );
  const hasMoreTools = visibleCount < toolCalls.length;
  const loadMoreTools = useCallback(() => {
    setVisibleCount((current) => Math.min(current + TRACE_RENDER_BATCH_SIZE, toolCalls.length));
  }, [toolCalls.length]);
  const handleToolListScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    const target = event.currentTarget;
    if (target.scrollTop + target.clientHeight >= target.scrollHeight - 80) {
      loadMoreTools();
    }
  }, [loadMoreTools]);

  useEffect(() => {
    setVisibleCount(TRACE_RENDER_BATCH_SIZE);
  }, [toolCalls]);

  return (
    <div className="mt-3 space-y-2">
      <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
        <Wrench size={12} />
        <span>{title}</span>
      </div>
      <div className={`${compact ? 'max-h-72' : 'max-h-96'} grid gap-2 overflow-y-auto pr-1`} onScroll={handleToolListScroll}>
        {visibleToolCalls.map((toolCall) => {
          const httpSummary = parseHttpToolSummary(toolCall.result);
          return (
            <div key={toolCall.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-2">
              <div className="flex min-w-0 items-center justify-between gap-2">
                <span className="min-w-0 truncate text-sm font-medium text-[var(--color-text-primary)]" title={normalizeToolDisplayName(toolCall.name)}>
                  {normalizeToolDisplayName(toolCall.name)}
                </span>
                <div className="flex shrink-0 items-center gap-1">
                  {(toolCall.args || toolCall.result) && (
                    <button
                      type="button"
                      className="inline-flex h-6 w-6 items-center justify-center rounded text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                      title="复制工具内容"
                      onClick={() => void copyTextToClipboard(toolCall.result || toolCall.args)}
                    >
                      <Copy size={12} />
                    </button>
                  )}
                  <Tag size="small" color={toolCall.status === 'failed' ? 'red' : toolCall.status === 'completed' ? 'green' : 'blue'}>
                    {toolCall.status}
                  </Tag>
                </div>
              </div>
              {httpSummary && (
                <div className="mt-2 grid gap-1 rounded bg-[var(--color-bg-secondary)] px-2 py-1.5 text-xs text-[var(--color-text-tertiary)]">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Tag size="small" color="blue">{httpSummary.method}</Tag>
                    <Tag size="small" color={httpSummary.statusCode >= 400 ? 'red' : 'green'}>{httpSummary.statusCode}</Tag>
                    {httpSummary.durationMs > 0 && <span>{httpSummary.durationMs}ms</span>}
                  </div>
                  <div className="truncate" title={httpSummary.url}>{httpSummary.url}</div>
                </div>
              )}
              {!compact && (toolCall.args || toolCall.result) && (
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-[var(--color-text-tertiary)]">
                  {formatLongTextPreview(toolCall.result || toolCall.args, 3000)}
                </pre>
              )}
            </div>
          );
        })}
        {hasMoreTools && (
          <button
            type="button"
            className="rounded-md bg-[var(--color-bg-primary)] p-2 text-center text-xs text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)]"
            onClick={loadMoreTools}
          >
            已显示 {visibleToolCalls.length}/{toolCalls.length} 次，继续滚动或点击加载 30 次
          </button>
        )}
      </div>
    </div>
  );
}
