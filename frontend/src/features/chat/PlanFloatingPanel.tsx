/** 当前会话最新 Plan 的页面级悬浮任务面板。 */

import {
  AlertCircle,
  CheckCircle2,
  Circle,
  CircleDot,
  Clock3,
  ListChecks,
  Loader2,
  PanelRightOpen,
  X,
} from 'lucide-react';
import { Tooltip } from '@/components/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import type { PlanReviewData } from './planReviewUtils';

const STATUS_LABELS: Record<string, string> = {
  drafting: '正在起草',
  revising: '正在修改',
  review_waiting: '等待确认',
  approved: '已确认',
  executing: '执行中',
  replanning: '重新规划',
  pending: '待执行',
  running: '执行中',
  completed: '已完成',
  skipped: '已跳过',
  failed: '失败',
  cancelled: '已取消',
};

const ACTIVE_STATUSES = new Set(['drafting', 'revising', 'approved', 'executing', 'replanning', 'running']);
const DONE_STEP_STATUSES = new Set(['completed', 'skipped']);

function stepText(step: Record<string, unknown>, field: string, fallback = ''): string {
  return typeof step[field] === 'string' ? step[field] : fallback;
}

function StatusIcon({ status, size = 14 }: { status: string; size?: number }) {
  if (ACTIVE_STATUSES.has(status)) return <Loader2 size={size} className="animate-spin" />;
  if (status === 'completed' || status === 'skipped') return <CheckCircle2 size={size} />;
  if (status === 'failed') return <AlertCircle size={size} />;
  if (status === 'review_waiting') return <Clock3 size={size} />;
  if (status === 'cancelled') return <CircleDot size={size} />;
  return <Circle size={size} />;
}

function statusTone(status: string): string {
  if (status === 'failed') return 'text-[var(--color-error)]';
  if (status === 'completed') return 'text-[var(--color-success)]';
  if (status === 'cancelled') return 'text-[var(--color-warning)]';
  if (ACTIVE_STATUSES.has(status)) return 'text-[var(--color-accent-text)]';
  return 'text-[var(--color-text-secondary)]';
}

export function PlanFloatingPanel({
  plan,
  open,
  onOpenChange,
}: {
  plan: PlanReviewData;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const completedCount = plan.steps.filter((step) => DONE_STEP_STATUSES.has(stepText(step, 'status'))).length;
  const progress = plan.steps.length > 0 ? Math.round((completedCount / plan.steps.length) * 100) : 0;
  const statusLabel = STATUS_LABELS[plan.status] || plan.status || '准备中';
  const showPlanDocument = ['drafting', 'revising', 'review_waiting'].includes(plan.status) || plan.steps.length === 0;

  if (!open) {
    return (
      <Tooltip title="打开计划面板" placement="left" mode="dark">
        <button
          type="button"
          className="absolute right-4 top-4 z-30 flex h-10 items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 text-xs font-medium text-[var(--color-text-secondary)] shadow-lg transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
          onClick={() => onOpenChange(true)}
          aria-label="打开计划面板"
        >
          <ListChecks size={16} className={statusTone(plan.status)} />
          <span>计划进度</span>
          {plan.steps.length > 0 ? <span className="text-[var(--color-text-tertiary)]">{completedCount}/{plan.steps.length}</span> : null}
          <PanelRightOpen size={14} />
        </button>
      </Tooltip>
    );
  }

  return (
    <aside
      className="absolute right-4 top-4 z-30 flex max-h-[calc(100%-2rem)] w-[390px] max-w-[calc(100%-2rem)] flex-col overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-primary)] shadow-2xl"
      aria-label="计划面板"
    >
      <header className="flex shrink-0 items-start gap-3 border-b border-[var(--color-border)] px-4 py-3">
        <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--color-bg-secondary)] ${statusTone(plan.status)}`}>
          <StatusIcon status={plan.status} size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{plan.title || '执行计划'}</div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
            <span className={statusTone(plan.status)}>{statusLabel}</span>
            <span>Revision {plan.revision}</span>
          </div>
        </div>
        <Tooltip title="关闭计划面板" placement="left" mode="dark">
          <button
            type="button"
            className="icon-button flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            onClick={() => onOpenChange(false)}
            aria-label="关闭计划面板"
          >
            <X size={16} />
          </button>
        </Tooltip>
      </header>

      {plan.steps.length > 0 ? (
        <div className="shrink-0 border-b border-[var(--color-border)] px-4 py-3">
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="text-[var(--color-text-secondary)]">整体进度</span>
            <span className="font-medium text-[var(--color-text-primary)]">{completedCount} / {plan.steps.length}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-bg-secondary)]">
            <div
              className="h-full rounded-full bg-[var(--color-accent)] transition-[width] duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {plan.goal && !showPlanDocument ? (
          <div className="mb-4 text-sm leading-6 text-[var(--color-text-secondary)]">{plan.goal}</div>
        ) : null}
        {plan.markdown && showPlanDocument ? (
          <div className="plan-floating-panel__markdown mb-4 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-sm">
            <MarkdownContent content={plan.markdown} />
          </div>
        ) : null}
        {plan.steps.length > 0 && !showPlanDocument ? (
          <div className="space-y-1" aria-label="计划步骤">
            {plan.steps.map((step, index) => {
              const status = stepText(step, 'status', 'pending');
              const detail = stepText(step, 'result', stepText(step, 'evidence'));
              return (
                <div key={stepText(step, 'id', String(index))} className="flex items-start gap-2.5 rounded-md px-2 py-2 hover:bg-[var(--color-bg-hover)]">
                  <span className={`mt-0.5 shrink-0 ${statusTone(status)}`}><StatusIcon status={status} size={14} /></span>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm leading-5 text-[var(--color-text-primary)]">{stepText(step, 'title', `步骤 ${index + 1}`)}</div>
                    {detail ? <div className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">{detail}</div> : null}
                  </div>
                  <span className={`shrink-0 text-[11px] ${statusTone(status)}`}>{STATUS_LABELS[status] || status}</span>
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
    </aside>
  );
}
