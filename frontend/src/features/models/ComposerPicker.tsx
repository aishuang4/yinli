/** 发送区下拉选择器。模型和审核模式共用这一套触发器与菜单。 */

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown } from 'lucide-react';

export interface ComposerPickerOption {
  id: string;
  label: string;
  description?: string;
  disabled?: boolean;
  title?: string;
  leading?: ReactNode;
}

export function ComposerPicker({
  ariaLabel,
  value,
  label,
  options,
  onChange,
  disabled = false,
  error,
  title,
  emptyText = '没有可选项',
  notice,
  footer,
  panelWidth = 300,
}: {
  ariaLabel: string;
  value: string;
  label: string;
  options: ComposerPickerOption[];
  onChange: (id: string) => void;
  disabled?: boolean;
  error?: string;
  title?: string;
  emptyText?: string;
  notice?: string;
  footer?: { label: string; icon?: ReactNode; onClick: () => void };
  panelWidth?: number;
}) {
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const highlightedRef = useRef<HTMLButtonElement>(null);
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    if (disabled && open) setOpen(false);
  }, [disabled, open]);

  useEffect(() => {
    if (!open) return;
    const selectedIndex = Math.max(0, options.findIndex((option) => option.id === value));
    setHighlightedIndex(selectedIndex);
    const frame = requestAnimationFrame(() => {
      const item = highlightedRef.current;
      const container = listRef.current;
      if (container && item) alignInView(container, item);
    });
    return () => cancelAnimationFrame(frame);
  }, [open, options, value]);

  useEffect(() => {
    if (!open) return;
    const item = highlightedRef.current;
    const container = listRef.current;
    if (!container || !item) return;
    alignInView(container, item);
  }, [highlightedIndex, open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (buttonRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
        return;
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setHighlightedIndex((index) => (options.length === 0 ? 0 : (index + 1) % options.length));
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setHighlightedIndex((index) => (
          options.length === 0 ? 0 : (index - 1 + options.length) % options.length
        ));
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        const next = options[highlightedIndex];
        if (!next || next.disabled) return;
        onChange(next.id);
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [highlightedIndex, onChange, open, options]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className={`inline-flex h-8 max-w-full items-center gap-1 rounded-lg px-1.5 text-sm transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-40 ${
          error
            ? 'text-[var(--color-error)]'
            : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
        }`}
        onClick={(event) => {
          setAnchorRect(event.currentTarget.getBoundingClientRect());
          setOpen((current) => !current);
        }}
        disabled={disabled}
        title={error || title}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="min-w-0 truncate">{label}</span>
        <ChevronDown size={14} className={`shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open ? createPortal(
        <ComposerPickerPanel
          panelRef={panelRef}
          listRef={listRef}
          highlightedRef={highlightedRef}
          anchorRect={anchorRect}
          ariaLabel={ariaLabel}
          options={options}
          selectedId={value}
          highlightedIndex={highlightedIndex}
          emptyText={emptyText}
          notice={notice}
          footer={footer}
          panelWidth={panelWidth}
          onHighlight={setHighlightedIndex}
          onPick={(id) => {
            onChange(id);
            setOpen(false);
          }}
          onFooter={() => {
            setOpen(false);
            footer?.onClick();
          }}
        />,
        document.body,
      ) : null}
    </>
  );
}

function ComposerPickerPanel({
  panelRef,
  listRef,
  highlightedRef,
  anchorRect,
  ariaLabel,
  options,
  selectedId,
  highlightedIndex,
  emptyText,
  notice,
  footer,
  panelWidth,
  onHighlight,
  onPick,
  onFooter,
}: {
  panelRef: { current: HTMLDivElement | null };
  listRef: { current: HTMLDivElement | null };
  highlightedRef: { current: HTMLButtonElement | null };
  anchorRect: DOMRect | null;
  ariaLabel: string;
  options: ComposerPickerOption[];
  selectedId: string;
  highlightedIndex: number;
  emptyText: string;
  notice?: string;
  footer?: { label: string; icon?: ReactNode; onClick: () => void };
  panelWidth: number;
  onHighlight: (index: number) => void;
  onPick: (id: string) => void;
  onFooter: () => void;
}) {
  const rect = anchorRect;
  let left = rect ? rect.right - panelWidth : 8;
  if (left < 8) left = 8;
  if (left + panelWidth > window.innerWidth - 8) left = window.innerWidth - 8 - panelWidth;
  const spaceAbove = rect?.top ?? 0;
  const spaceBelow = rect ? window.innerHeight - rect.bottom : 0;
  const openUp = spaceAbove >= 280 || spaceAbove > spaceBelow;
  const style = openUp
    ? { left, bottom: window.innerHeight - (rect?.top ?? 0) + 8, width: panelWidth }
    : { left, top: (rect?.bottom ?? 0) + 8, width: panelWidth };

  return (
    <div
      ref={panelRef}
      className="fixed z-[70] overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-primary)] shadow-[0_18px_44px_rgba(0,0,0,0.18)]"
      style={style}
      role="listbox"
      aria-label={ariaLabel}
    >
      {notice ? (
        <div className="border-b border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-tertiary)]">{notice}</div>
      ) : null}
      <div ref={listRef} className="max-h-80 overflow-y-auto px-1 py-1">
        {options.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-[var(--color-text-tertiary)]">{emptyText}</div>
        ) : options.map((option, index) => {
          const active = index === highlightedIndex;
          const checked = option.id === selectedId;
          return (
            <button
              key={option.id}
              ref={active ? highlightedRef : undefined}
              type="button"
              role="option"
              aria-selected={checked}
              aria-disabled={option.disabled}
              disabled={option.disabled}
              title={option.title}
              className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left disabled:cursor-not-allowed disabled:opacity-45 ${
                active ? 'bg-[var(--color-bg-hover)]' : 'hover:bg-[var(--color-bg-hover)]'
              }`}
              onMouseEnter={() => onHighlight(index)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onPick(option.id)}
            >
              {option.leading}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-[var(--color-text-primary)]">{option.label}</span>
                {option.description ? (
                  <span className="mt-0.5 block text-xs leading-5 text-[var(--color-text-tertiary)]">{option.description}</span>
                ) : null}
              </span>
              {checked ? <Check size={14} className="shrink-0 text-[var(--color-text-secondary)]" /> : null}
            </button>
          );
        })}
      </div>
      {footer ? (
        <button
          type="button"
          className="flex w-full items-center gap-2 border-t border-[var(--color-border)] px-3 py-2.5 text-left text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
          onMouseDown={(event) => event.preventDefault()}
          onClick={onFooter}
        >
          {footer.icon}
          {footer.label}
        </button>
      ) : null}
    </div>
  );
}

function alignInView(container: HTMLElement, item: HTMLElement): void {
  const containerRect = container.getBoundingClientRect();
  const itemRect = item.getBoundingClientRect();
  if (itemRect.top < containerRect.top) {
    container.scrollTop -= containerRect.top - itemRect.top;
    return;
  }
  if (itemRect.bottom > containerRect.bottom) {
    container.scrollTop += itemRect.bottom - containerRect.bottom;
  }
}
