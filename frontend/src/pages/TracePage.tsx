/**
 * TracePage — 运行轨迹列表与详情加载。进行中 Run 短轮询详情，不接 Chat SSE。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, RefreshCw } from 'lucide-react';
import { Button, Spinner } from '@/components/design';
import { WorkbenchDetailShell } from '../components/workbench';
import { TraceDetailView } from '../features/traces/TraceDetailView';
import { TraceLandingPage } from '../features/traces/TraceLandingPage';
import { ACTIVE_TRACE_STATUSES, type TraceDetail, type TraceSummary } from '../features/traces/traceTypes';
import { formatDuration } from '../features/traces/traceUtils';
import { useAppStore } from '../stores/appStore';

const DETAIL_POLL_MS = 3000;

export function TracePage() {
  const active = useAppStore((state) => state.currentView === 'traces');
  const activeProjectSlug = useAppStore((state) => state.activeProjectSlug);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
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
      const params = new URLSearchParams({ projectSlug: activeProjectSlug });
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
  }, [active, activeProjectSlug]);

  const loadDetail = useCallback(async (traceId: string, silent = false) => {
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
    if (!silent) {
      setDetailLoading(true);
      setDetail(null);
    }
    try {
      const params = new URLSearchParams({ projectSlug: activeProjectSlug });
      const response = await fetch(`/api/traces/${traceId}?${params.toString()}`, { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json() as TraceDetail;
      if (detailRequestSeq.current !== requestSeq) return;
      setDetail(data);
      setDetailLoading(false);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      if (detailRequestSeq.current !== requestSeq) return;
      setError(err instanceof Error ? err.message : '加载运行轨迹详情失败');
      setDetailLoading(false);
    }
  }, [active, activeProjectSlug]);

  useEffect(() => {
    setSelectedId('');
    setDetail(null);
    setTraces([]);
  }, [activeProjectSlug]);

  useEffect(() => {
    if (!active) return;
    void loadTraces();
  }, [active, loadTraces]);

  useEffect(() => {
    if (!active) {
      tracesAbortRef.current?.abort();
      detailAbortRef.current?.abort();
      setDetailLoading(false);
      return;
    }
    void loadDetail(selectedId);
  }, [active, loadDetail, selectedId]);

  useEffect(() => {
    if (!active || !detail || !ACTIVE_TRACE_STATUSES.has(detail.status)) return;
    const timer = window.setInterval(() => {
      void loadDetail(selectedId, true);
    }, DETAIL_POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, detail, loadDetail, selectedId]);

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
      subtitle={selectedTrace
        ? `${selectedTrace.mode === 'team' ? `${selectedTrace.agentName || 'Agent'} · Team Leader` : selectedTrace.agentName || selectedId} · ${formatDuration(selectedTrace.durationMs)}`
        : selectedId}
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
          <TraceDetailView trace={detail} />
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
