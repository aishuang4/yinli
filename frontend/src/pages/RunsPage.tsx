/**
 * RunsPage — 运行任务中心。
 *
 * 第一层展示运行任务列表；点击任务进入二级详情页查看进度和最终结果。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RunDetailPage } from '../features/runs/RunDetailPage';
import { RunLandingPage } from '../features/runs/RunLandingPage';
import type { RunDetail, RunSummary } from '../features/runs/runTypes';
import { ACTIVE_RUN_STATUSES } from '../features/runs/runUtils';
import { useAppStore } from '../stores/appStore';

export function RunsPage() {
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busyRunId, setBusyRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const runsAbortRef = useRef<AbortController | null>(null);

  const selectedRun = useMemo(() => (
    runs.find((run) => run.id === selectedRunId) || null
  ), [runs, selectedRunId]);
  const runningRuns = useMemo(() => (
    runs.filter((run) => ACTIVE_RUN_STATUSES.has(run.status))
  ), [runs]);
  const recentRuns = useMemo(() => (
    runs.filter((run) => !ACTIVE_RUN_STATUSES.has(run.status))
  ), [runs]);

  const loadRuns = useCallback(async (showRefreshState = false) => {
    runsAbortRef.current?.abort();
    const controller = new AbortController();
    runsAbortRef.current = controller;
    try {
      if (showRefreshState) setRefreshing(true);
      setError(null);
      const params = new URLSearchParams({ projectId: activeProjectId });
      const response = await fetch(`/api/chat/runs?${params.toString()}`, { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data: { runs: RunSummary[] } = await response.json();
      setRuns(data.runs);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : '运行任务加载失败');
    } finally {
      if (!controller.signal.aborted) {
        setInitialLoading(false);
        if (showRefreshState) setRefreshing(false);
      }
    }
  }, [activeProjectId]);

  const cancelRun = useCallback(async (runId: string) => {
    try {
      setBusyRunId(runId);
      const params = new URLSearchParams({ projectId: activeProjectId });
      const response = await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/cancel?${params.toString()}`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const updated: RunSummary = await response.json();
      setRuns((current) => current.map((run) => (run.id === runId ? updated : run)));
    } catch (err) {
      setError(err instanceof Error ? err.message : '取消运行失败');
    } finally {
      setBusyRunId(null);
    }
  }, [activeProjectId]);

  const rerunRun = useCallback(async (runId: string) => {
    try {
      setBusyRunId(runId);
      const params = new URLSearchParams({ projectId: activeProjectId });
      const response = await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/rerun?${params.toString()}`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const created: RunSummary = await response.json();
      setRuns((current) => [created, ...current.filter((run) => run.id !== created.id)]);
      setSelectedRunId(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : '重新运行失败');
    } finally {
      setBusyRunId(null);
    }
  }, [activeProjectId]);

  useEffect(() => {
    setSelectedRunId(null);
    setDetail(null);
    setRuns([]);
    setInitialLoading(true);
  }, [activeProjectId]);

  useEffect(() => {
    void loadRuns();
    const intervalMs = runningRuns.length > 0 ? 5000 : 15000;
    const interval = window.setInterval(() => void loadRuns(), intervalMs);
    return () => {
      window.clearInterval(interval);
      runsAbortRef.current?.abort();
    };
  }, [loadRuns, runningRuns.length]);

  useEffect(() => {
    if (!selectedRun) {
      setDetail(null);
      return;
    }
    setDetail({ ...selectedRun, events: [] });
    setDetailLoading(false);
  }, [selectedRun]);

  if (!selectedRunId) {
    return (
      <RunLandingPage
        runs={runs}
        runningRuns={runningRuns}
        recentRuns={recentRuns}
        loading={initialLoading}
        refreshing={refreshing}
        error={error}
        busyRunId={busyRunId}
        onRefresh={() => void loadRuns(true)}
        onSelect={setSelectedRunId}
        onCancel={(runId) => void cancelRun(runId)}
        onRerun={(runId) => void rerunRun(runId)}
      />
    );
  }

  return (
    <RunDetailPage
      run={detail}
      loading={detailLoading}
      busy={busyRunId === selectedRunId}
      onBack={() => {
        setSelectedRunId(null);
        setDetail(null);
      }}
      onCancel={() => void cancelRun(selectedRunId)}
      onRerun={() => void rerunRun(selectedRunId)}
    />
  );
}
