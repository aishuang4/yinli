/**
 * RunDetailPage — 单个后台运行任务详情。
 */

import { Ban, CheckCircle2, Clock3, RotateCcw } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import { WorkbenchDetailShell } from '../../components/workbench';
import type { RunDetail } from './runTypes';
import { ACTIVE_RUN_STATUSES, clampProgress, formatDateTime, formatDuration, runDisplayName, statusText } from './runUtils';
import { ProgressBar, RunStatusTag, SummaryItem } from './RunDisplayParts';

interface RunDetailPageProps {
  run: RunDetail | null;
  loading: boolean;
  busy: boolean;
  onBack: () => void;
  onCancel: () => void;
  onRerun: () => void;
}

export function RunDetailPage({
  run,
  loading,
  busy,
  onBack,
  onCancel,
  onRerun,
}: RunDetailPageProps) {
  return (
    <WorkbenchDetailShell
      title={run ? runDisplayName(run) : '运行任务'}
      subtitle={run ? `${run.mode === 'team' ? 'Team 协作' : '单 Agent'} · 开始于 ${formatDateTime(run.createdAt)}` : '运行任务详情'}
      onBack={onBack}
      actions={run ? (
        <>
          {run.status === 'running' ? (
            <Button danger size="small" icon={<Ban size={14} />} loading={busy} onClick={onCancel}>
              取消运行
            </Button>
          ) : null}
          {!ACTIVE_RUN_STATUSES.has(run.status) ? (
            <Button size="small" icon={<RotateCcw size={14} />} loading={busy} onClick={onRerun}>
              {run.status === 'failed' ? '重试' : '重新运行'}
            </Button>
          ) : null}
        </>
      ) : null}
    >
      <div className="h-full min-h-0 overflow-y-auto bg-[var(--color-bg-primary)] px-5 py-5">
        {loading ? (
          <div className="flex h-full min-h-[360px] items-center justify-center">
            <Spinner />
          </div>
        ) : run ? (
          <RunDashboard run={run} />
        ) : (
          <RunDetailEmpty description="运行详情不存在" />
        )}
      </div>
    </WorkbenchDetailShell>
  );
}

function RunDetailEmpty({ description }: { description: string }) {
  return (
    <div className="flex h-full min-h-[360px] items-center justify-center">
      <Empty type="noData" description={description} />
    </div>
  );
}

function RunDashboard({ run }: { run: RunDetail }) {
  const duration = formatDuration(run.createdAt, run.completedAt);
  const activeActors = run.activeActors.length > 0
    ? run.activeActors
    : [runDisplayName(run)].filter(Boolean);

  return (
    <div className="mx-auto max-w-5xl space-y-4">
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
          label="Member"
          value={run.memberTotal > 0 ? `${run.memberCompleted}/${run.memberTotal} 完成` : '无'}
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
          {run.resultLength > 0 ? (
            <span className="text-xs font-normal text-[var(--color-text-tertiary)]">
              {run.resultLength} 字
            </span>
          ) : null}
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
