/**
 * TracePage — 运行轨迹页面。
 *
 * 只负责运行轨迹列表、详情和事件分页的数据加载；展示组件放在 features/traces。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity } from 'lucide-react';
import { Spinner } from '@agentscope-ai/design';
import { TraceDetailView } from '../features/traces/TraceDetailView';
import { TraceListSidebar } from '../features/traces/TraceListSidebar';
import type { TraceDetail, TraceEvent, TraceSummary } from '../features/traces/traceTypes';
import { TRACE_RENDER_BATCH_SIZE } from '../features/traces/traceUtils';
import { useAppStore } from '../stores/appStore';

export function TracePage() {
  const active = useAppStore((state) => state.currentView === 'traces');
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [eventPageLoading, setEventPageLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tracesAbortRef = useRef<AbortController | null>(null);
  const detailRequestSeq = useRef(0);
  const detailAbortRef = useRef<AbortController | null>(null);

  const loadTraces = useCallback(async () => {
    if (!active) return;
    tracesAbortRef.current?.abort();
    const controller = new AbortController();
    tracesAbortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/traces', { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data: TraceSummary[] = await response.json();
      setTraces(data);
      setSelectedId((current) => current || data[0]?.id || '');
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : '加载运行轨迹失败');
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, [active]);

  const loadDetail = useCallback(async (traceId: string) => {
    if (!active) return;
    const requestSeq = detailRequestSeq.current + 1;
    detailRequestSeq.current = requestSeq;
    detailAbortRef.current?.abort();
    if (!traceId) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    detailAbortRef.current = controller;
    setDetailLoading(true);
    setDetail(null);
    try {
      const response = await fetch(`/api/traces/${traceId}`, { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json() as TraceDetail;
      window.setTimeout(() => {
        if (detailRequestSeq.current !== requestSeq) return;
        setDetail(data);
        setDetailLoading(false);
      }, 0);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      if (detailRequestSeq.current !== requestSeq) return;
      setError(err instanceof Error ? err.message : '加载运行轨迹详情失败');
      setDetailLoading(false);
    }
  }, [active]);

  const loadMoreTraceEvents = useCallback(async () => {
    if (!active || !detail || eventPageLoading || detail.events.length >= detail.eventTotal) return;
    const offset = detail.events.length;
    setEventPageLoading(true);
    try {
      const response = await fetch(`/api/traces/${encodeURIComponent(detail.id)}/events?offset=${offset}&limit=${TRACE_RENDER_BATCH_SIZE}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json() as { events: TraceEvent[]; total: number };
      setDetail((current) => {
        if (!current || current.id !== detail.id) return current;
        const existingIds = new Set(current.events.map((event) => event.id));
        const nextEvents = data.events.filter((event) => !existingIds.has(event.id));
        return {
          ...current,
          events: [...current.events, ...nextEvents],
          eventTotal: data.total,
        };
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载运行轨迹事件失败');
    } finally {
      setEventPageLoading(false);
    }
  }, [active, detail, eventPageLoading]);

  useEffect(() => {
    if (!active) return;
    void loadTraces();
  }, [active, loadTraces]);

  useEffect(() => {
    if (!active) {
      tracesAbortRef.current?.abort();
      detailAbortRef.current?.abort();
      setDetailLoading(false);
      setEventPageLoading(false);
      return;
    }
    void loadDetail(selectedId);
  }, [active, loadDetail, selectedId]);

  useEffect(() => () => {
    tracesAbortRef.current?.abort();
    detailAbortRef.current?.abort();
  }, []);

  return (
    <div className="grid h-full min-h-0 grid-cols-[340px_1fr] gap-4 overflow-hidden p-4">
      <TraceListSidebar
        traces={traces}
        selectedId={selectedId}
        loading={loading}
        error={error}
        onSelect={setSelectedId}
        onRefresh={() => void loadTraces()}
      />

      <main className="min-h-0 overflow-hidden rounded-md bg-[var(--color-bg-primary)] shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
        {detailLoading ? (
          <div className="flex h-full items-center justify-center"><Spinner /></div>
        ) : detail ? (
          <TraceDetailView
            trace={detail}
            eventPageLoading={eventPageLoading}
            onLoadMoreEvents={loadMoreTraceEvents}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-[var(--color-text-tertiary)]">
            <div className="text-center">
              <Activity size={44} strokeWidth={1.2} className="mx-auto mb-3" />
              <div>选择一条运行轨迹</div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
