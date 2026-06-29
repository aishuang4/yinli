/**
 * RunsPage — 运行任务中心。
 *
 * 只展示“谁在运行、完成百分比、当前状态、最终结果”。
 * 具体事件、工具调用和 block 级细节放到运行轨迹页面查看。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Ban, Bot, CheckCircle2, Clock3, RefreshCw, RotateCcw, Users } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../components/MarkdownContent';

const ACTIVE_RUN_STATUSES = new Set(['running', 'waiting', 'cancelling']);

interface RunSummary {
  id: string;
  sessionId: string;
  agentId: string;
  agentName: string;
  teamId: string;
  teamName: string;
  task: string;
  mode: string;
  status: string;
  createdAt: string;
  completedAt: string | null;
  eventCount: number;
  subscriberCount: number;
  latestEvent: string;
  progress: number;
  currentStage: string;
  activeActors: string[];
  resultPreview: string;
  resultLength: number;
  workerTotal: number;
  workerCompleted: number;
}

interface RunDetail extends RunSummary {
  events: string[];
}

export function RunsPage() {
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
      const response = await fetch('/api/chat/runs', { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data: { runs: RunSummary[] } = await response.json();
      setRuns(data.runs);
      setSelectedRunId((current) => current || data.runs[0]?.id || null);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : '运行任务加载失败');
    } finally {
      if (!controller.signal.aborted) {
        setInitialLoading(false);
        if (showRefreshState) setRefreshing(false);
      }
    }
  }, []);

  const cancelRun = useCallback(async (runId: string) => {
    try {
      setBusyRunId(runId);
      const response = await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/cancel`, {
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
  }, []);

  const rerunRun = useCallback(async (runId: string) => {
    try {
      setBusyRunId(runId);
      const response = await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/rerun`, {
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
  }, []);

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

  return (
    <div className="grid h-full min-h-0 grid-cols-[390px_minmax(0,1fr)] gap-4 bg-[var(--color-bg-secondary)] p-4">
      <section className="grid min-h-0 grid-rows-[auto_1fr] rounded-xl bg-[var(--color-bg-primary)] shadow-sm">
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-[var(--color-text-primary)]">运行任务</h2>
            <div className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
              查看正在运行的 Agent 和 Team
            </div>
          </div>
          <Button
            size="small"
            icon={<RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />}
            onClick={() => void loadRuns(true)}
          />
        </div>

        <div className="min-h-0 overflow-y-auto px-3 pb-3">
          {error && (
            <div className="mb-3 rounded-md border border-[var(--color-error)] px-3 py-2 text-sm text-[var(--color-error)]">
              {error}
            </div>
          )}
          {initialLoading ? (
            <div className="flex justify-center py-10">
              <Spinner />
            </div>
          ) : runs.length === 0 ? (
            <Empty type="noData" description="暂无运行任务" />
          ) : (
            <div className="space-y-4">
              <RunGroup
                title="正在处理"
                emptyText="当前没有正在处理的任务"
                runs={runningRuns}
                selectedRunId={selectedRunId}
                busyRunId={busyRunId}
                onSelect={setSelectedRunId}
                onCancel={cancelRun}
                onRerun={rerunRun}
              />
              <RunGroup
                title="最近完成"
                emptyText="暂无最近完成任务"
                runs={recentRuns}
                selectedRunId={selectedRunId}
                busyRunId={busyRunId}
                onSelect={setSelectedRunId}
                onCancel={cancelRun}
                onRerun={rerunRun}
              />
            </div>
          )}
        </div>
      </section>

      <section className="grid min-h-0 grid-rows-[auto_1fr] rounded-xl bg-[var(--color-bg-primary)] shadow-sm">
        <div className="flex items-start justify-between gap-3 px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-[var(--color-text-primary)]">
              {selectedRun ? runDisplayName(selectedRun) : '任务详情'}
            </h2>
            {selectedRun && (
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
                <RunStatusTag status={selectedRun.status} />
                <span>{selectedRun.mode === 'team' ? 'Team 协作' : '单 Agent'}</span>
                <span>开始于 {formatDateTime(selectedRun.createdAt)}</span>
              </div>
            )}
          </div>
          {selectedRun && (
            <div className="flex shrink-0 gap-2">
              {selectedRun.status === 'running' && (
                <Button
                  danger
                  size="small"
                  icon={<Ban size={14} />}
                  loading={busyRunId === selectedRun.id}
                  onClick={() => void cancelRun(selectedRun.id)}
                >
                  取消运行
                </Button>
              )}
              {!ACTIVE_RUN_STATUSES.has(selectedRun.status) && (
                <Button
                  size="small"
                  icon={<RotateCcw size={14} />}
                  loading={busyRunId === selectedRun.id}
                  onClick={() => void rerunRun(selectedRun.id)}
                >
                  {selectedRun.status === 'failed' ? '重试' : '重新运行'}
                </Button>
              )}
            </div>
          )}
        </div>

        <div className="min-h-0 overflow-y-auto px-5 pb-5">
          {!selectedRunId ? (
            <RunDetailEmpty description="选择左侧任务查看详情" />
          ) : detailLoading ? (
            <div className="flex h-full items-center justify-center">
              <Spinner />
            </div>
          ) : detail ? (
            <RunDashboard run={detail} />
          ) : (
            <RunDetailEmpty description="运行详情不存在" />
          )}
        </div>
      </section>
    </div>
  );
}

function RunDetailEmpty({ description }: { description: string }) {
  return (
    <div className="flex h-full min-h-[360px] items-center justify-center">
      <Empty type="noData" description={description} />
    </div>
  );
}

function RunGroup({
  title,
  emptyText,
  runs,
  selectedRunId,
  busyRunId,
  onSelect,
  onCancel,
  onRerun,
}: {
  title: string;
  emptyText: string;
  runs: RunSummary[];
  selectedRunId: string | null;
  busyRunId: string | null;
  onSelect: (runId: string) => void;
  onCancel: (runId: string) => void;
  onRerun: (runId: string) => void;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-xs text-[var(--color-text-tertiary)]">
        <span>{title}</span>
        <span>{runs.length}</span>
      </div>
      {runs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-[var(--color-border)] px-3 py-4 text-center text-xs text-[var(--color-text-tertiary)]">
          {emptyText}
        </div>
      ) : (
        <div className="space-y-2">
          {runs.map((run) => (
            <RunListItem
              key={run.id}
              run={run}
              selected={run.id === selectedRunId}
              busy={busyRunId === run.id}
              onSelect={() => onSelect(run.id)}
              onCancel={() => void onCancel(run.id)}
              onRerun={() => void onRerun(run.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function RunListItem({
  run,
  selected,
  busy,
  onSelect,
  onCancel,
  onRerun,
}: {
  run: RunSummary;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  onCancel: () => void;
  onRerun: () => void;
}) {
  const active = ACTIVE_RUN_STATUSES.has(run.status);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      className={`w-full rounded-lg border px-3 py-3 text-left transition ${
        selected
          ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg)]'
          : 'border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)]'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 gap-3">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-primary)] text-[var(--color-text-tertiary)]">
            {run.mode === 'team' ? <Users size={16} /> : <Bot size={16} />}
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {runDisplayName(run)}
            </div>
            <div className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--color-text-secondary)]">
              {run.task || '未记录任务内容'}
            </div>
          </div>
        </div>
        <RunStatusTag status={run.status} />
      </div>
      <div className="mt-3">
        <ProgressBar value={run.progress} />
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-xs text-[var(--color-text-tertiary)]">
        <span className="truncate">{run.currentStage || statusText(run.status)}</span>
        <span>{clampProgress(run.progress)}%</span>
      </div>
      {active && run.status === 'running' && (
        <div className="mt-3 flex justify-end">
          <Button
            danger
            size="small"
            loading={busy}
            onClick={(event) => {
              event.stopPropagation();
              onCancel();
            }}
          >
            取消
          </Button>
        </div>
      )}
      {!active && (
        <div className="mt-3 flex justify-end">
          <Button
            size="small"
            icon={<RotateCcw size={13} />}
            loading={busy}
            onClick={(event) => {
              event.stopPropagation();
              onRerun();
            }}
          >
            {run.status === 'failed' ? '重试' : '重新运行'}
          </Button>
        </div>
      )}
    </div>
  );
}

function RunDashboard({ run }: { run: RunDetail }) {
  const duration = formatDuration(run.createdAt, run.completedAt);
  const activeActors = run.activeActors.length > 0
    ? run.activeActors
    : [runDisplayName(run)].filter(Boolean);

  return (
    <div className="space-y-4">
      <div className="rounded-xl bg-[var(--color-bg-secondary)] p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="text-xs text-[var(--color-text-tertiary)]">当前进度</div>
            <div className="mt-1 text-3xl font-semibold text-[var(--color-text-primary)]">
              {clampProgress(run.progress)}%
            </div>
          </div>
          <RunStatusTag status={run.status} />
        </div>
        <div className="mt-4">
          <ProgressBar value={run.progress} large />
        </div>
        <div className="mt-3 text-sm text-[var(--color-text-secondary)]">
          {run.currentStage || statusText(run.status)}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        <SummaryItem label="执行对象" value={runDisplayName(run)} />
        <SummaryItem label="运行方式" value={run.mode === 'team' ? 'Team 协作' : '单 Agent'} />
        <SummaryItem label="耗时" value={duration || '进行中'} />
        <SummaryItem
          label="Worker"
          value={run.workerTotal > 0 ? `${run.workerCompleted}/${run.workerTotal} 完成` : '无'}
        />
      </div>

      <section className="rounded-xl bg-[var(--color-bg-secondary)] p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Clock3 size={16} />
          谁在运行
        </div>
        <div className="flex flex-wrap gap-2">
          {activeActors.map((actor) => (
            <Tag key={actor} size="small" color="blue">{actor}</Tag>
          ))}
        </div>
      </section>

      <section className="rounded-xl bg-[var(--color-bg-secondary)] p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <CheckCircle2 size={16} />
          结果
          {run.resultLength > 0 && (
            <span className="text-xs font-normal text-[var(--color-text-tertiary)]">
              {run.resultLength} 字
            </span>
          )}
        </div>
        {run.resultPreview ? (
          <div className="max-h-[360px] overflow-auto rounded-lg bg-[var(--color-bg-primary)] px-3 py-3 text-sm leading-6 text-[var(--color-text-primary)]">
            <MarkdownContent content={run.resultPreview} />
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-[var(--color-border)] px-3 py-6 text-center text-sm text-[var(--color-text-tertiary)]">
            {ACTIVE_RUN_STATUSES.has(run.status) ? '结果生成中' : '暂无结果摘要'}
          </div>
        )}
      </section>

      <section className="rounded-xl bg-[var(--color-bg-secondary)] p-4">
        <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">任务内容</div>
        <div className="whitespace-pre-wrap rounded-lg bg-[var(--color-bg-primary)] px-3 py-3 text-sm leading-6 text-[var(--color-text-primary)]">
          {run.task || '未记录任务内容'}
        </div>
      </section>
    </div>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-[var(--color-bg-secondary)] px-3 py-3">
      <div className="text-xs text-[var(--color-text-tertiary)]">{label}</div>
      <div className="mt-1 truncate text-sm font-medium text-[var(--color-text-primary)]" title={value}>
        {value}
      </div>
    </div>
  );
}

function ProgressBar({ value, large = false }: { value: number; large?: boolean }) {
  return (
    <div className={`overflow-hidden rounded-full bg-[var(--color-bg-primary)] ${large ? 'h-3' : 'h-2'}`}>
      <div
        className="h-full rounded-full bg-[var(--color-accent)] transition-all duration-500"
        style={{ width: `${clampProgress(value)}%` }}
      />
    </div>
  );
}

function RunStatusTag({ status }: { status: string }) {
  const color = (
    status === 'running' || status === 'cancelling'
      ? 'blue'
      : status === 'waiting'
        ? 'orange'
        : status === 'completed'
          ? 'green'
          : status === 'failed'
            ? 'orange'
            : 'transparent'
  );
  return <Tag size="small" color={color}>{statusText(status)}</Tag>;
}

function statusText(status: string) {
  if (status === 'running') return '运行中';
  if (status === 'waiting') return '等待人工处理';
  if (status === 'cancelling') return '取消中';
  if (status === 'cancelled') return '已取消';
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '失败';
  return status || '未知';
}

function runDisplayName(run: RunSummary) {
  if (run.mode === 'team') {
    return run.teamName || run.agentName || '未命名 Team';
  }
  return run.agentName || '未命名 Agent';
}

function formatDateTime(value: string) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatDuration(start: string, end: string | null) {
  if (!start) return '';
  const startTime = new Date(start).getTime();
  const endTime = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(startTime) || Number.isNaN(endTime) || endTime < startTime) return '';
  const seconds = Math.round((endTime - startTime) / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const restSeconds = seconds % 60;
  if (minutes < 60) return `${minutes} 分 ${restSeconds} 秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
}

function clampProgress(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}
