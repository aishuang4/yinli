/**
 * WorkbenchDetailShell — 管理页第二层详情外壳。
 *
 * 提供统一返回导航和对象级操作区，详情内容由 children 注入。
 */

import type { ReactNode } from 'react';
import { ArrowLeft } from 'lucide-react';
import { Button } from '@/components/design';

export function WorkbenchDetailShell({
  title,
  subtitle,
  children,
  onBack,
  actions,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onBack: () => void;
  actions?: ReactNode;
}) {
  return (
    <div className="grid h-full min-h-0 min-w-0 grid-rows-[auto_1fr] overflow-hidden bg-[var(--color-bg-secondary)]">
      <header className="flex min-w-0 items-center justify-between gap-4 border-b border-[var(--color-border)] bg-[var(--color-bg-primary)] px-6 py-3">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Button size="small" icon={<ArrowLeft size={14} />} onClick={onBack}>
            返回
          </Button>
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold text-[var(--color-text-primary)]">{title}</h1>
            {subtitle ? <p className="mt-0.5 truncate text-xs text-[var(--color-text-tertiary)]">{subtitle}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">{actions}</div> : null}
      </header>
      <div className="min-h-0 min-w-0 overflow-hidden">
        {children}
      </div>
    </div>
  );
}
