/**
 * MemoryDetailBlocks — 记忆页面详情块和文本列表。
 */

import type { ReactNode } from 'react';

export function MetricBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
      <div className="text-xs text-[var(--color-text-tertiary)]">{label}</div>
      <div className="mt-1 text-sm font-semibold text-[var(--color-text-primary)]">{value}</div>
    </div>
  );
}

export function DetailSection({
  title,
  icon,
  children,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-md bg-[var(--color-bg-secondary)] p-3 text-sm leading-6 text-[var(--color-text-secondary)]">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-[var(--color-text-primary)]">
        {icon}
        {title}
      </div>
      {children}
    </section>
  );
}

export function NodeTextList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="min-w-0 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
      <div className="mb-1 text-xs font-medium text-[var(--color-text-tertiary)]">{title}</div>
      {items.length === 0 ? (
        <div className="text-xs text-[var(--color-text-tertiary)]">空</div>
      ) : (
        <div className="space-y-1">
          {items.slice(0, 3).map((item, index) => (
            <div key={`${title}-${index}`} className="line-clamp-3 text-xs leading-5 text-[var(--color-text-secondary)]">
              {item}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
