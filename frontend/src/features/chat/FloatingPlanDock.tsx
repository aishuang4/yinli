import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ListChecks, X } from 'lucide-react';
import type { ChatRunPlanState } from '../../hooks/useEventStream';
import { PlanDisplayBlock } from './PlanDisplayBlock';
import type { ChatPlanBlock } from './chatDisplayTypes';

function isActivePlan(plan: ChatRunPlanState): boolean {
  const status = String(plan.status || '').toLowerCase();
  return status === 'running' || status === 'pending';
}

function toPlanBlock(plan: ChatRunPlanState): ChatPlanBlock {
  return {
    id: plan.planId ? `plan-${plan.planId}` : 'plan-current',
    type: 'plan',
    status: plan.status || 'running',
    planId: plan.planId,
    planFile: plan.planFile,
    markdown: plan.markdown,
    steps: plan.steps,
    updatedAt: plan.updatedAt,
  };
}

function activePlanSummary(plan: ChatRunPlanState): string {
  if (plan.steps.length === 0) return '生成中';
  const completed = plan.steps.filter((step) => {
    const status = String(step.status || '');
    return status === 'completed' || status === 'skipped';
  }).length;
  return `${completed}/${plan.steps.length}`;
}

export function FloatingPlanDock({ plans }: { plans: ChatRunPlanState[] }) {
  const activePlan = useMemo(
    () => plans.find(isActivePlan) || null,
    [plans],
  );
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!activePlan) {
      setOpen(false);
    }
  }, [activePlan]);

  if (!activePlan) return null;

  const planBlock = toPlanBlock(activePlan);

  return (
    <div className="pointer-events-none absolute right-5 top-4 z-20 flex max-w-[min(520px,calc(100%_-_40px))] flex-col items-end gap-2">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="pointer-events-auto inline-flex h-11 items-center gap-2 rounded-full border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-4 text-sm font-medium text-[var(--color-text-primary)] shadow-lg transition hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)]"
        aria-expanded={open}
      >
        <ListChecks size={17} className="text-[var(--color-primary)]" />
        <span>Plan</span>
        <span className="rounded-full bg-[var(--color-bg-tertiary)] px-2 py-0.5 text-xs text-[var(--color-text-secondary)]">
          {activePlanSummary(activePlan)}
        </span>
        <ChevronDown
          size={15}
          className={`text-[var(--color-text-tertiary)] transition ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="pointer-events-auto w-[min(520px,calc(100vw_-_40px))] overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-primary)] shadow-xl sm:min-w-[360px]">
          <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-3 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <ListChecks size={15} className="shrink-0 text-[var(--color-primary)]" />
              <div className="min-w-0 truncate text-sm font-medium text-[var(--color-text-primary)]">
                Plan Mode
              </div>
            </div>
            <button
              type="button"
              title="关闭 Plan"
              aria-label="关闭 Plan"
              onClick={() => setOpen(false)}
              className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            >
              <X size={14} />
            </button>
          </div>
          <div className="max-h-[min(62vh,620px)] overflow-auto">
            <PlanDisplayBlock block={planBlock} compact />
          </div>
        </div>
      )}
    </div>
  );
}
