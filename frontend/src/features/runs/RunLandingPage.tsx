/**
 * RunLandingPage — Runs 第一层运行任务列表。
 */

import { Ban, Bot, Clock3, List, RefreshCw, RotateCcw, Users } from 'lucide-react';
import { Button, Tag } from '@agentscope-ai/design';
import {
  WorkbenchEmptyPanel,
  WorkbenchList,
  WorkbenchListIcon,
  WorkbenchListPage,
  WorkbenchListRow,
  WorkbenchLoadingPanel,
  WorkbenchSection,
} from '../../components/workbench';
import type { RunSummary } from './runTypes';
import { ACTIVE_RUN_STATUSES, clampProgress, runDisplayName, statusText } from './runUtils';
import { ProgressBar, RunStatusTag } from './RunDisplayParts';

interface RunLandingPageProps {
  runs: RunSummary[];
  runningRuns: RunSummary[];
  recentRuns: RunSummary[];
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  busyRunId: string | null;
  onRefresh: () => void;
  onSelect: (runId: string) => void;
  onCancel: (runId: string) => void;
  onRerun: (runId: string) => void;
}

export function RunLandingPage({
  runs,
  runningRuns,
  recentRuns,
  loading,
  refreshing,
  error,
  busyRunId,
  onRefresh,
  onSelect,
  onCancel,
  onRerun,
}: RunLandingPageProps) {
  return (
    <WorkbenchListPage
      title="运行任务"
      icon={<Clock3 size={18} />}
      actions={(
        <>
          <Tag size="small" color="blue">{runs.length} 个任务</Tag>
          <Tag size="small" color="green">{runningRuns.length} 个处理中</Tag>
          <Button
            icon={<RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />}
            onClick={onRefresh}
            loading={refreshing}
          >
            刷新
          </Button>
        </>
      )}
    >
      {error ? (
        <div className="mb-5">
          <WorkbenchEmptyPanel>
            <span className="text-[var(--color-error)]">{error}</span>
          </WorkbenchEmptyPanel>
        </div>
      ) : null}

      {loading ? (
        <WorkbenchLoadingPanel tip="加载运行任务..." />
      ) : runs.length === 0 ? (
        <WorkbenchEmptyPanel>暂无运行任务</WorkbenchEmptyPanel>
      ) : (
        <>
          <RunGroup
            title="正在处理"
            emptyText="当前没有正在处理的任务"
            runs={runningRuns}
            busyRunId={busyRunId}
            onSelect={onSelect}
            onCancel={onCancel}
            onRerun={onRerun}
          />
          <RunGroup
            title="最近完成"
            compact
            emptyText="暂无最近完成任务"
            runs={recentRuns}
            busyRunId={busyRunId}
            onSelect={onSelect}
            onCancel={onCancel}
            onRerun={onRerun}
          />
        </>
      )}
    </WorkbenchListPage>
  );
}

function RunGroup({
  title,
  emptyText,
  runs,
  compact = false,
  busyRunId,
  onSelect,
  onCancel,
  onRerun,
}: {
  title: string;
  emptyText: string;
  runs: RunSummary[];
  compact?: boolean;
  busyRunId: string | null;
  onSelect: (runId: string) => void;
  onCancel: (runId: string) => void;
  onRerun: (runId: string) => void;
}) {
  return (
    <WorkbenchSection
      title={title}
      compact={compact}
      actions={<span className="text-xs text-[var(--color-text-tertiary)]">{runs.length}</span>}
    >
      {runs.length === 0 ? (
        <WorkbenchEmptyPanel>{emptyText}</WorkbenchEmptyPanel>
      ) : (
        <WorkbenchList>
          {runs.map((run, index) => (
            <RunListRow
              key={run.id}
              run={run}
              busy={busyRunId === run.id}
              last={index === runs.length - 1}
              onSelect={() => onSelect(run.id)}
              onCancel={() => onCancel(run.id)}
              onRerun={() => onRerun(run.id)}
            />
          ))}
        </WorkbenchList>
      )}
    </WorkbenchSection>
  );
}

function RunListRow({
  run,
  busy,
  last,
  onSelect,
  onCancel,
  onRerun,
}: {
  run: RunSummary;
  busy: boolean;
  last: boolean;
  onSelect: () => void;
  onCancel: () => void;
  onRerun: () => void;
}) {
  const active = ACTIVE_RUN_STATUSES.has(run.status);

  return (
    <WorkbenchListRow
      icon={(
        <WorkbenchListIcon>
          {run.mode === 'team' ? <Users size={20} strokeWidth={1.8} /> : <Bot size={20} strokeWidth={1.8} />}
        </WorkbenchListIcon>
      )}
      title={runDisplayName(run)}
      subtitle={run.task || '未记录任务内容'}
      description={run.currentStage || statusText(run.status)}
      last={last}
      layout="columns"
      onSelect={onSelect}
      meta={(
        <>
          <RunStatusTag status={run.status} />
          <Tag size="small" color="transparent">{clampProgress(run.progress)}%</Tag>
          <Tag size="small" color="transparent">{run.mode === 'team' ? 'Team 协作' : '单 Agent'}</Tag>
        </>
      )}
      trailing={(
        <div className="flex w-40 shrink-0 items-center justify-end gap-3 lg:w-[19rem]">
          <div className="hidden w-24 shrink-0 lg:block">
            <ProgressBar value={run.progress} />
          </div>
          <button
            type="button"
            onClick={onSelect}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            aria-label={`打开 ${runDisplayName(run)} 详情`}
          >
            <List size={17} />
          </button>
          {active && run.status === 'running' ? (
            <Button className="w-28 justify-center" size="small" danger icon={<Ban size={13} />} loading={busy} onClick={onCancel}>
              取消
            </Button>
          ) : null}
          {!active ? (
            <Button className="w-28 justify-center" size="small" icon={<RotateCcw size={13} />} loading={busy} onClick={onRerun}>
              {run.status === 'failed' ? '重试' : '重新运行'}
            </Button>
          ) : null}
        </div>
      )}
    />
  );
}
