/**
 * RunDisplayParts — Runs 页面共享展示小组件。
 */

import { Tag } from '@agentscope-ai/design';
import { clampProgress, statusText } from './runUtils';

export function ProgressBar({ value, large = false }: { value: number; large?: boolean }) {
  return (
    <div className={`overflow-hidden rounded-full bg-[var(--color-bg-primary)] ${large ? 'h-3' : 'h-2'}`}>
      <div
        className="h-full rounded-full bg-[var(--color-accent)] transition-all duration-500"
        style={{ width: `${clampProgress(value)}%` }}
      />
    </div>
  );
}

export function RunStatusTag({ status }: { status: string }) {
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

export function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-[var(--color-bg-secondary)] px-3 py-3">
      <div className="text-xs text-[var(--color-text-tertiary)]">{label}</div>
      <div className="mt-1 truncate text-sm font-medium text-[var(--color-text-primary)]" title={value}>
        {value}
      </div>
    </div>
  );
}
