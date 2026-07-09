/**
 * WorkbenchListPage — 管理页第一层列表布局组件。
 *
 * 用于 Agents、Subagents、MCP、Skills 等对象管理页面，保持同一套入口列表观感。
 */

import type { ReactNode } from 'react';
import { Spinner } from '@agentscope-ai/design';
import { cn } from '../../utils/cn';

export function WorkbenchListPage({
  title,
  icon,
  actions,
  children,
}: {
  title: string;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="h-full min-h-0 overflow-auto bg-[var(--color-bg-primary)]">
      <main className="mx-auto w-full max-w-5xl px-8 py-14">
        <header className="mb-8 flex items-center justify-between gap-6">
          <div className="flex min-w-0 items-center gap-2">
            <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">{title}</h1>
            {icon ? <span className="shrink-0 text-[var(--color-text-tertiary)]">{icon}</span> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
        {children}
      </main>
    </div>
  );
}

export function WorkbenchSection({
  title,
  compact = false,
  actions,
  children,
}: {
  title: string;
  compact?: boolean;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={compact ? '' : 'mb-8'}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className={cn(
          'font-semibold text-[var(--color-text-primary)]',
          compact ? 'text-base' : 'text-lg',
        )}>
          {title}
        </h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function WorkbenchList({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
      {children}
    </div>
  );
}

export function WorkbenchListRow({
  icon,
  title,
  subtitle,
  description,
  meta,
  last,
  onSelect,
  trailing,
}: {
  icon: ReactNode;
  title: string;
  subtitle?: string;
  description?: string;
  meta?: ReactNode;
  last?: boolean;
  onSelect?: () => void;
  trailing?: ReactNode;
}) {
  const content = (
    <>
      {icon}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-base font-semibold text-[var(--color-text-primary)]">{title}</span>
        {subtitle ? (
          <span className="mt-1 block truncate text-xs text-[var(--color-text-tertiary)]">{subtitle}</span>
        ) : null}
      </span>
      {description ? (
        <span className="hidden min-w-0 flex-[1.2] truncate text-sm font-medium text-[var(--color-text-secondary)] md:block">
          {description}
        </span>
      ) : null}
      {meta ? <span className="hidden shrink-0 items-center gap-1 md:flex">{meta}</span> : null}
    </>
  );

  return (
    <div className={cn('group flex items-center gap-3 px-4 py-4', !last && 'border-b border-[var(--color-border)]')}>
      {onSelect ? (
        <button
          type="button"
          onClick={onSelect}
          className="flex min-w-0 flex-1 items-center gap-4 text-left"
        >
          {content}
        </button>
      ) : (
        <div className="flex min-w-0 flex-1 items-center gap-4 text-left">
          {content}
        </div>
      )}
      {trailing}
    </div>
  );
}

export function WorkbenchLoadingPanel({ tip = '加载中...' }: { tip?: string }) {
  return (
    <div className="flex justify-center rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] py-8">
      <Spinner tip={tip} />
    </div>
  );
}

export function WorkbenchEmptyPanel({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick?: () => void;
}) {
  if (!onClick) {
    return (
      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-5 py-7 text-sm font-medium text-[var(--color-text-tertiary)]">
        {children}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-5 py-7 text-left text-sm font-medium text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
    >
      {children}
    </button>
  );
}
