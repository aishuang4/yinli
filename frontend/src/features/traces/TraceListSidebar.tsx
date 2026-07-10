/**
 * TraceListSidebar — 运行轨迹列表侧栏。
 */

import { RefreshCw } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@agentscope-ai/design';
import type { TraceSummary } from './traceTypes';

interface TraceListSidebarProps {
  traces: TraceSummary[];
  selectedId: string;
  loading: boolean;
  error: string | null;
  onSelect: (traceId: string) => void;
  onRefresh: () => void;
}

export function TraceListSidebar({
  traces,
  selectedId,
  loading,
  error,
  onSelect,
  onRefresh,
}: TraceListSidebarProps) {
  return (
    <aside className="flex min-h-0 flex-col rounded-md bg-[var(--color-bg-primary)] p-4 shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-medium text-[var(--color-text-primary)]">运行轨迹</h2>
          <div className="mt-1 text-xs text-[var(--color-text-secondary)]">
            {traces.length} 条历史运行
          </div>
        </div>
        <Button size="small" icon={<RefreshCw size={14} />} onClick={onRefresh} />
      </div>

      {loading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : error ? (
        <div className="rounded-md border border-[var(--color-border)] p-3 text-sm text-[var(--color-error)]">{error}</div>
      ) : traces.length === 0 ? (
        <Empty type="noData" description="暂无运行轨迹" />
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
          {traces.map((trace) => (
            <button
              key={trace.id}
              type="button"
              onClick={() => onSelect(trace.id)}
              className={[
                'ui-card-transition w-full rounded-md border p-3 text-left',
                selectedId === trace.id
                  ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg)]'
                  : 'border-[var(--color-border)] bg-[var(--color-bg-primary)] hover:bg-[var(--color-bg-hover)]',
              ].join(' ')}
            >
              <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">{trace.title}</div>
              <div className="mt-1 truncate text-xs text-[var(--color-text-secondary)]">
                {trace.teamName || trace.agentName || 'Agent'}
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                <Tag size="small" color={trace.status === 'failed' ? 'red' : trace.status === 'waiting' ? 'orange' : 'green'}>
                  {trace.status}
                </Tag>
                <Tag size="small" color="transparent">{trace.messageCount} 消息</Tag>
                <Tag size="small" color="transparent">{trace.toolCallCount} 工具</Tag>
                {trace.memberCount > 0 && <Tag size="small" color="transparent">{trace.memberCount} Member</Tag>}
              </div>
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}
