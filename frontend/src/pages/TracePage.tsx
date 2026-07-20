/**
 * TracePage — 运行轨迹页面。
 *
 * 只负责运行轨迹列表、详情和事件分页的数据加载；展示组件放在 features/traces。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, RefreshCw } from 'lucide-react';
import { Button, Spinner } from '@agentscope-ai/design';
import { WorkbenchDetailShell } from '../components/workbench';
import { TraceDetailView } from '../features/traces/TraceDetailView';
import { TraceLandingPage } from '../features/traces/TraceLandingPage';
import type { TraceDetail, TraceEvent, TraceSummary } from '../features/traces/traceTypes';
import { TRACE_RENDER_BATCH_SIZE } from '../features/traces/traceUtils';
import { useAppStore } from '../stores/appStore';

export function TracePage() {
  const active = useAppStore((state) => state.currentView === 'traces');
  const activeProjectId = useAppStore((state) => state.activeProjectId);
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
      const params = new URLSearchParams({ projectId: activeProjectId });
      const response = await fetch(`/api/traces?${params.toString()}`, { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data: TraceSummary[] = await response.json();
      setTraces(data);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : '加载运行轨迹失败');
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, [active, activeProjectId]);

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
      const params = new URLSearchParams({ projectId: activeProjectId });
      const response = await fetch(`/api/traces/${traceId}?${params.toString()}`, { signal: controller.signal });
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
  }, [active, activeProjectId]);

  const loadMoreTraceEvents = useCallback(async () => {
    if (!active || !detail || eventPageLoading || detail.events.length >= detail.eventTotal) return;
    const offset = detail.events.length;
    setEventPageLoading(true);
    try {
      const params = new URLSearchParams({
        projectId: activeProjectId,
        offset: String(offset),
        limit: String(TRACE_RENDER_BATCH_SIZE),
      });
      const response = await fetch(`/api/traces/${encodeURIComponent(detail.id)}/events?${params.toString()}`);
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
  }, [active, activeProjectId, detail, eventPageLoading]);

  useEffect(() => {
    setSelectedId('');
    setDetail(null);
    setTraces([]);
  }, [activeProjectId]);

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

  if (!selectedId) {
    return (
      <TraceLandingPage
        traces={traces}
        loading={loading}
        error={error}
        onSelect={setSelectedId}
        onRefresh={() => void loadTraces()}
      />
    );
  }

  const selectedTrace = detail || traces.find((trace) => trace.id === selectedId) || null;

  return (
    <WorkbenchDetailShell
      title={selectedTrace?.title || '运行轨迹'}
      subtitle={selectedTrace?.mode === 'team' ? `${selectedTrace.agentName || 'Agent'} · Team Leader` : selectedTrace?.agentName || selectedId}
      onBack={() => {
        setSelectedId('');
        setDetail(null);
      }}
      actions={(
        <Button size="small" icon={<RefreshCw size={13} />} onClick={() => void loadDetail(selectedId)} loading={detailLoading}>
          刷新
        </Button>
      )}
    >
      <main className="h-full min-h-0 overflow-hidden bg-[var(--color-bg-primary)]">
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
              <div>运行轨迹详情加载失败</div>
            </div>
          </div>
        )}
      </main>
    </WorkbenchDetailShell>
  );
}
