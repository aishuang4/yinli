/** Chat / Sandbox / 群聊共用的轻量模型选择器。 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown, Settings } from 'lucide-react';
import { useAppStore } from '../../stores/appStore';
import { modelAvailable, type ModelCatalog } from './useModelCatalog';

const ADAPTER_MARK: Record<string, string> = {
  openai: 'O',
  deepseek: 'D',
  dashscope: 'Q',
  moonshot: 'K',
  gemini: 'G',
  anthropic: 'A',
  ollama: 'L',
};

export type SelectableModel = {
  id: string;
  label: string;
  adapter: string;
  providerName: string;
};

export function listSelectableModels(catalog: ModelCatalog | null): SelectableModel[] {
  if (!catalog) return [];
  const items: SelectableModel[] = [];
  for (const provider of catalog.providers) {
    for (const model of catalog.models) {
      if (model.providerId !== provider.id || !modelAvailable(catalog, model)) continue;
      items.push({
        id: model.id,
        label: model.label || model.model,
        adapter: provider.adapter,
        providerName: provider.name,
      });
    }
  }
  return items;
}

export function ModelSelector({
  catalog,
  value,
  onChange,
  disabled,
  loading,
  error,
}: {
  catalog: ModelCatalog | null;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  loading?: boolean;
  error?: string;
}) {
  const setView = useAppStore((state) => state.setView);
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const highlightedRef = useRef<HTMLButtonElement>(null);
  const models = useMemo(() => listSelectableModels(catalog), [catalog]);
  const selected = models.find((model) => model.id === value) ?? models[0];
  const triggerLabel = error
    ? '模型配置异常'
    : loading
      ? '加载中…'
      : selected?.label || '选择模型';

  useEffect(() => {
    if (disabled && open) setOpen(false);
  }, [disabled, open]);

  useEffect(() => {
    if (!open) return;
    const selectedIndex = Math.max(0, models.findIndex((model) => model.id === (selected?.id || value)));
    setHighlightedIndex(selectedIndex);
    const frame = requestAnimationFrame(() => {
      const item = highlightedRef.current;
      const container = listRef.current;
      if (container && item) alignInView(container, item);
    });
    return () => cancelAnimationFrame(frame);
  }, [open, models, selected?.id, value]);

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
        setHighlightedIndex((index) => (models.length === 0 ? 0 : (index + 1) % models.length));
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setHighlightedIndex((index) => (
          models.length === 0 ? 0 : (index - 1 + models.length) % models.length
        ));
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        const next = models[highlightedIndex];
        if (!next) return;
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
  }, [highlightedIndex, models, onChange, open]);

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
        onClick={() => setOpen((current) => !current)}
        disabled={disabled || loading}
        title={error || undefined}
        aria-label="选择模型"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="min-w-0 truncate">{triggerLabel}</span>
        <ChevronDown size={14} className={`shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open ? createPortal(
        <ModelMenuPanel
          panelRef={panelRef}
          listRef={listRef}
          highlightedRef={highlightedRef}
          anchor={buttonRef.current}
          models={models}
          selectedId={selected?.id || value}
          highlightedIndex={highlightedIndex}
          onHighlight={setHighlightedIndex}
          onPick={(id) => {
            onChange(id);
            setOpen(false);
          }}
          onAdd={() => {
            setOpen(false);
            setView('models');
          }}
        />,
        document.body,
      ) : null}
    </>
  );
}

function ModelMenuPanel({
  panelRef,
  listRef,
  highlightedRef,
  anchor,
  models,
  selectedId,
  highlightedIndex,
  onHighlight,
  onPick,
  onAdd,
}: {
  panelRef: { current: HTMLDivElement | null };
  listRef: { current: HTMLDivElement | null };
  highlightedRef: { current: HTMLButtonElement | null };
  anchor: HTMLButtonElement | null;
  models: SelectableModel[];
  selectedId: string;
  highlightedIndex: number;
  onHighlight: (index: number) => void;
  onPick: (id: string) => void;
  onAdd: () => void;
}) {
  const rect = anchor?.getBoundingClientRect();
  const width = 300;
  let left = rect ? rect.right - width : 8;
  if (left < 8) left = 8;
  if (left + width > window.innerWidth - 8) left = window.innerWidth - 8 - width;
  const spaceAbove = rect?.top ?? 0;
  const spaceBelow = rect ? window.innerHeight - rect.bottom : 0;
  const openUp = spaceAbove >= 280 || spaceAbove > spaceBelow;
  const style = openUp
    ? { left, bottom: window.innerHeight - (rect?.top ?? 0) + 8, width }
    : { left, top: (rect?.bottom ?? 0) + 8, width };

  return (
    <div
      ref={panelRef}
      className="fixed z-[70] overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-primary)] shadow-[0_18px_44px_rgba(0,0,0,0.18)]"
      style={style}
      role="listbox"
      aria-label="选择模型"
    >
      <div ref={listRef} className="max-h-80 overflow-y-auto px-1 py-1">
        {models.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-[var(--color-text-tertiary)]">还没有可用模型</div>
        ) : models.map((model, index) => {
          const active = index === highlightedIndex;
          const checked = model.id === selectedId;
          return (
            <button
              key={model.id}
              ref={active ? highlightedRef : undefined}
              type="button"
              role="option"
              aria-selected={checked}
              className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left ${
                active ? 'bg-[var(--color-bg-hover)]' : 'hover:bg-[var(--color-bg-hover)]'
              }`}
              onMouseEnter={() => onHighlight(index)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onPick(model.id)}
            >
              <AdapterMark adapter={model.adapter} name={model.providerName} />
              <span className="min-w-0 flex-1 truncate text-sm text-[var(--color-text-primary)]">{model.label}</span>
              {checked ? <Check size={14} className="shrink-0 text-[var(--color-text-secondary)]" /> : null}
            </button>
          );
        })}
      </div>
      <button
        type="button"
        className="flex w-full items-center gap-2 border-t border-[var(--color-border)] px-3 py-2.5 text-left text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
        onMouseDown={(event) => event.preventDefault()}
        onClick={onAdd}
      >
        <Settings size={14} />
        添加模型
      </button>
    </div>
  );
}

function AdapterMark({ adapter, name }: { adapter: string; name: string }) {
  const mark = ADAPTER_MARK[adapter] || name.trim().slice(0, 1).toUpperCase() || '?';
  return (
    <span
      className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--color-bg-hover)] text-[10px] font-medium text-[var(--color-text-secondary)]"
      aria-hidden="true"
    >
      {mark}
    </span>
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
