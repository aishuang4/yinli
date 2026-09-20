/**
 * WorkbenchListPage — 管理页第一层列表布局组件。
 *
 * 用于 Agents、Subagents、MCP、Skills 等对象管理页面，保持同一套入口列表观感。
 */

import type { ReactNode } from 'react';
import { Spinner } from '@/components/design';
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

/** 管理列表统一图标容器，使用主题色保证亮暗模式对比一致。 */
export function WorkbenchListIcon({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn(
      'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--color-accent-bg)] text-[var(--color-accent-text)]',
      className,
    )}>
      {children}
    </span>
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
  layout = 'default',
}: {
  icon: ReactNode;
  title: string;
  subtitle?: string;
  description?: string;
  meta?: ReactNode;
  last?: boolean;
  onSelect?: () => void;
  trailing?: ReactNode;
  /** 数据列表使用固定列轨，避免名称、描述和标签随内容长度错位。 */
  layout?: 'default' | 'columns';
}) {
  const identity = (
    <span className="min-w-0">
      <span className="block truncate text-base font-semibold text-[var(--color-text-primary)]">{title}</span>
      {subtitle ? (
        <span className="mt-1 block truncate text-xs text-[var(--color-text-tertiary)]">{subtitle}</span>
      ) : null}
    </span>
  );

  const columnsClassName = meta
    ? 'lg:grid-cols-[2.5rem_minmax(10rem,14rem)_minmax(0,1fr)_minmax(16rem,19rem)]'
    : 'lg:grid-cols-[2.5rem_minmax(10rem,14rem)_minmax(0,1fr)]';

  const content = layout === 'columns' ? (
    <span className={cn('grid min-w-0 flex-1 grid-cols-[2.5rem_minmax(0,1fr)] items-center gap-x-4', columnsClassName)}>
      {icon}
      {identity}
      {description ? (
        <span className="hidden min-w-0 truncate text-sm font-medium text-[var(--color-text-secondary)] lg:block">
          {description}
        </span>
      ) : null}
      {meta ? <span className="hidden shrink-0 items-center justify-end gap-1 lg:flex">{meta}</span> : null}
    </span>
  ) : (
    <>
      {icon}
      <span className="min-w-0 flex-1">{identity}</span>
      {description ? (
        <span className="hidden min-w-0 flex-[1.2] truncate text-sm font-medium text-[var(--color-text-secondary)] md:block">
          {description}
        </span>
      ) : null}
      {meta ? <span className="hidden shrink-0 items-center gap-1 md:flex">{meta}</span> : null}
    </>
  );

  const contentClassName = layout === 'columns'
    ? 'min-w-0 flex-1 text-left'
    : 'flex min-w-0 flex-1 items-center gap-4 text-left';

  return (
    <div className={cn('group flex items-center gap-3 px-4 py-4', !last && 'border-b border-[var(--color-border)]')}>
      {onSelect ? (
        <button
          type="button"
          onClick={onSelect}
          className={contentClassName}
        >
          {content}
        </button>
      ) : (
        <div className={contentClassName}>
          {content}
        </div>
      )}
      {trailing ? <span className="flex shrink-0 items-center">{trailing}</span> : null}
    </div>
  );
}

export function WorkbenchLoadingPanel({ tip = '加载中...' }: { tip?: string }) {
  return (
    <div className="flex justify-center rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] py-8">
      <Spinner tip={tip}>
        <div className="min-h-12 w-12" />
      </Spinner>
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
