/**
 * TraceDetailView — 单次运行轨迹详情。
 */

import { Tag } from '@/components/design';
import type { TraceDetail } from './traceTypes';
import { TraceEventTimeline } from './TraceEventTimeline';
import { TraceMessageCard } from './TraceMessageCard';
import { TraceStatus } from './TraceStatus';

interface TraceDetailViewProps {
  trace: TraceDetail;
  eventPageLoading: boolean;
  onLoadMoreEvents: () => void;
}

export function TraceDetailView({
  trace,
  eventPageLoading,
  onLoadMoreEvents,
}: TraceDetailViewProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-medium text-[var(--color-text-primary)]">{trace.title}</h2>
            <div className="mt-1 text-sm text-[var(--color-text-secondary)]">
              {trace.mode === 'team' ? `${trace.agentName || 'Agent'} · Team Leader` : trace.agentName}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <TraceStatus status={trace.status} />
            <Tag size="small" color="transparent">{trace.toolCallCount} 工具调用</Tag>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 pb-5">
        {trace.events && trace.events.length > 0 && (
          <TraceEventTimeline
            events={trace.events}
            eventTotal={trace.eventTotal}
            loadingMore={eventPageLoading}
            onLoadMore={onLoadMoreEvents}
          />
        )}
        {trace.messages.map((message) => (
          <TraceMessageCard key={message.id} message={message} />
        ))}
      </div>
    </div>
  );
}
