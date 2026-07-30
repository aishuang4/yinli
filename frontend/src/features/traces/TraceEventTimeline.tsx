/**
 * TraceEventTimeline — 运行轨迹事件时间线。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { UIEvent } from 'react';
import { Activity, ShieldCheck } from 'lucide-react';
import { Tag } from '@/components/design';
import type { TraceEvent, TraceEventFilter } from './traceTypes';
import {
  formatPayloadPreview,
  formatTraceTime,
  getRecord,
  isPermissionEvent,
  isRuntimeEvent,
  matchesTraceFilter,
  permissionDecisionColor,
  shouldShowTracePayload,
  TRACE_FILTERS,
  TRACE_RENDER_BATCH_SIZE,
} from './traceUtils';

interface TraceEventTimelineProps {
  events: TraceEvent[];
  eventTotal: number;
  loadingMore: boolean;
  onLoadMore: () => void;
}

export function TraceEventTimeline({
  events,
  eventTotal,
  loadingMore,
  onLoadMore,
}: TraceEventTimelineProps) {
  const [filter, setFilter] = useState<TraceEventFilter>('all');
  const [visibleCount, setVisibleCount] = useState(TRACE_RENDER_BATCH_SIZE);
  const filteredEvents = useMemo(
    () => events.filter((event) => matchesTraceFilter(event, filter)),
    [events, filter],
  );
  const visibleEvents = useMemo(
    () => filteredEvents.slice(0, visibleCount),
    [filteredEvents, visibleCount],
  );
  const hasMoreLocalEvents = visibleCount < filteredEvents.length;
  const hasMoreRemoteEvents = events.length < eventTotal;
  const permissionCount = useMemo(
    () => events.filter((event) => matchesTraceFilter(event, 'permissions')).length,
    [events],
  );
  const runtimeCount = useMemo(
    () => events.filter((event) => matchesTraceFilter(event, 'runtime')).length,
    [events],
  );
  const loadMoreEvents = useCallback(() => {
    if (hasMoreLocalEvents) {
      setVisibleCount((current) => Math.min(current + TRACE_RENDER_BATCH_SIZE, filteredEvents.length));
      return;
    }
    if (hasMoreRemoteEvents && !loadingMore) {
      onLoadMore();
    }
  }, [filteredEvents.length, hasMoreLocalEvents, hasMoreRemoteEvents, loadingMore, onLoadMore]);
  const handleEventListScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    const target = event.currentTarget;
    if (target.scrollTop + target.clientHeight >= target.scrollHeight - 80) {
      loadMoreEvents();
    }
  }, [loadMoreEvents]);

  useEffect(() => {
    setVisibleCount(TRACE_RENDER_BATCH_SIZE);
  }, [filter, events]);

  return (
    <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Activity size={15} color="var(--color-accent)" />
          <span>事件时间线</span>
        </div>
        <div className="flex items-center gap-2">
          {runtimeCount > 0 && <Tag size="small" color="blue">{runtimeCount} Runtime</Tag>}
          {permissionCount > 0 && <Tag size="small" color="green">{permissionCount} 权限</Tag>}
          <Tag size="small" color="transparent">{events.length}/{eventTotal} 事件</Tag>
        </div>
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        {TRACE_FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setFilter(item.key)}
            className={[
              'ui-card-transition rounded-md px-3 py-1.5 text-xs',
              filter === item.key
                ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]'
                : 'bg-[var(--color-bg-primary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
            ].join(' ')}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1" onScroll={handleEventListScroll}>
        {visibleEvents.map((event) => (
          <TraceEventRow key={event.id} event={event} />
        ))}
        {(hasMoreLocalEvents || hasMoreRemoteEvents) && (
          <button
            type="button"
            className="w-full rounded-md bg-[var(--color-bg-primary)] p-3 text-center text-xs text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)]"
            onClick={loadMoreEvents}
            disabled={loadingMore}
          >
            {loadingMore
              ? '正在加载更多事件...'
              : `已显示 ${visibleEvents.length}/${eventTotal} 条，继续滚动或点击加载 30 条`}
          </button>
        )}
        {filteredEvents.length === 0 && (
          <div className="rounded-md bg-[var(--color-bg-primary)] p-6 text-center text-sm text-[var(--color-text-tertiary)]">
            当前筛选下暂无事件
          </div>
        )}
      </div>
    </section>
  );
}

function TraceEventRow({ event }: { event: TraceEvent }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {event.title || event.eventType}
            </span>
            <Tag size="small" color={event.scope === 'team_member' ? 'mauve' : 'blue'}>{event.scope}</Tag>
            {event.status && (
              <Tag size="small" color={event.status === 'failed' ? 'red' : event.status === 'waiting' ? 'orange' : event.status === 'completed' ? 'green' : 'transparent'}>
                {event.status}
              </Tag>
            )}
          </div>
          <div className="mt-1 flex min-w-0 flex-wrap gap-2 text-xs text-[var(--color-text-tertiary)]">
            <span>{event.eventType}</span>
            {isRuntimeEvent(event) && <span>{String(event.payload.type || '')}</span>}
            {event.agentName && <span>{event.agentName}</span>}
          </div>
        </div>
        <time className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
          {formatTraceTime(event.createdAt)}
        </time>
      </div>
      {event.content && (
        <div className="mt-2 line-clamp-3 whitespace-pre-wrap break-words text-xs text-[var(--color-text-secondary)]">
          {event.content}
        </div>
      )}
      {isPermissionEvent(event) && <PermissionAuditSummary event={event} />}
      {shouldShowTracePayload(event) && (
        <pre className="mt-2 max-h-40 overflow-auto rounded-md bg-[var(--color-bg-secondary)] p-2 text-[11px] text-[var(--color-text-tertiary)]">
          {formatPayloadPreview(event.payload)}
        </pre>
      )}
    </div>
  );
}

function PermissionAuditSummary({ event }: { event: TraceEvent }) {
  const payload = event.payload;
  const policy = getRecord(payload.policy);
  const decision = String(payload.decision || event.status || '');
  const resourceType = String(payload.resourceType || '');
  const resourceId = String(payload.resourceId || '');
  const action = String(payload.action || '');
  const reason = String(payload.reason || '');
  const toolPolicy = String(policy.toolPolicy || payload.toolPolicy || '');
  const networkEnabled = policy.networkEnabled;

  return (
    <div className="mt-3 rounded-md bg-[var(--color-bg-secondary)] p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <ShieldCheck size={14} color="var(--color-accent)" />
        <Tag size="small" color={permissionDecisionColor(decision)}>{decision || 'configured'}</Tag>
        {resourceType && <Tag size="small" color="transparent">{resourceType}</Tag>}
        {action && <Tag size="small" color="transparent">{action}</Tag>}
        {toolPolicy && <Tag size="small" color="blue">{toolPolicy}</Tag>}
        {typeof networkEnabled === 'boolean' && (
          <Tag size="small" color={networkEnabled ? 'green' : 'orange'}>
            network {networkEnabled ? 'on' : 'off'}
          </Tag>
        )}
      </div>
      {resourceId && (
        <div className="truncate text-sm font-medium text-[var(--color-text-primary)]" title={resourceId}>
          {resourceId}
        </div>
      )}
      {reason && (
        <div className="mt-1 line-clamp-2 text-xs text-[var(--color-text-secondary)]">
          {reason}
        </div>
      )}
    </div>
  );
}
