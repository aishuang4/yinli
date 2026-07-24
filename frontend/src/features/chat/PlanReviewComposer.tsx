/** Plan revision 的确认、自然语言修改和取消入口。 */

import { useState } from 'react';
import { Check, PencilLine, Send, X } from 'lucide-react';
import type { PlanReviewData } from './planReviewUtils';

type ReviewAction = 'revise' | 'approve' | 'cancel';

export function PlanReviewComposer({
  plan,
  submitting,
  onSubmit,
}: {
  plan: PlanReviewData;
  submitting: boolean;
  onSubmit: (action: ReviewAction, feedback?: string) => Promise<void>;
}) {
  const [feedback, setFeedback] = useState('');
  const [activeAction, setActiveAction] = useState<ReviewAction | null>(null);
  const isBusy = submitting || activeAction !== null;

  const submit = async (action: ReviewAction) => {
    if (action === 'revise' && !feedback.trim()) return;
    setActiveAction(action);
    try {
      await onSubmit(action, feedback.trim());
    } finally {
      setActiveAction(null);
    }
  };

  return (
    <div className="px-8 pb-6 pt-2">
      <div className="mx-auto max-w-4xl rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-primary)] shadow-sm">
        <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-3">
          <PencilLine size={15} className="text-[var(--color-text-secondary)]" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">审核 {plan.title}</div>
            <div className="text-xs text-[var(--color-text-tertiary)]">Revision {plan.revision} · {plan.steps.length} 个步骤</div>
          </div>
          <button
            type="button"
            className="inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-xs text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40"
            disabled={isBusy}
            onClick={() => { void submit('cancel'); }}
          >
            <X size={14} />取消计划
          </button>
          <button
            type="button"
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-[var(--color-text-primary)] px-3 text-xs text-[var(--color-bg-primary)] disabled:opacity-40"
            disabled={isBusy}
            onClick={() => { void submit('approve'); }}
          >
            <Check size={14} />{activeAction === 'approve' ? '正在启动' : '确认并执行'}
          </button>
        </div>
        <div className="flex items-end gap-3 p-3">
          <textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="告诉模型需要怎样修改这份计划..."
            rows={2}
            disabled={isBusy}
            className="min-h-[58px] flex-1 resize-none bg-transparent px-1 py-1 text-sm text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)]"
          />
          <button
            type="button"
            title="提交修改意见"
            aria-label="提交修改意见"
            className="icon-button flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40"
            disabled={isBusy || !feedback.trim()}
            onClick={() => { void submit('revise'); }}
          >
            <Send size={15} />
          </button>
        </div>
      </div>
    </div>
  );
}
