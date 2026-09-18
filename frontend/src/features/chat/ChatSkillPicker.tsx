/** Chat 发送框 Skill 选择：/ 唤起列表，按钮插入 /，选中后以标签强制开启。 */

import { useEffect, useRef } from 'react';
import { BookOpen, X } from 'lucide-react';
import type { Skill } from '../../types/skill';
import { MAX_FORCED_SKILLS } from './forcedSkills';
import { COMPOSER_STATUS_CHIP_CLASS } from './plusMenu';

type ScrollBox = {
  scrollTop: number;
  getBoundingClientRect: () => Pick<DOMRect, 'top' | 'bottom'>;
};

type HighlightBox = {
  getBoundingClientRect: () => Pick<DOMRect, 'top' | 'bottom'>;
};

/** 只滚动 Skill 列表容器，避免 scrollIntoView 带动整页。 */
export function alignSkillHighlightInView(container: ScrollBox, item: HighlightBox): void {
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

export function ChatSkillSlashMenu({
  skills,
  selectedIds,
  highlightedIndex,
  loading = false,
  query,
  onHighlight,
  onPick,
}: {
  skills: Skill[];
  selectedIds: string[];
  highlightedIndex: number;
  loading?: boolean;
  query: string;
  onHighlight: (index: number) => void;
  onPick: (skill: Skill) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const highlightedRef = useRef<HTMLButtonElement>(null);
  const selectedSet = new Set(selectedIds);
  const atLimit = selectedIds.length >= MAX_FORCED_SKILLS;

  useEffect(() => {
    const container = listRef.current;
    const item = highlightedRef.current;
    if (!container || !item) return;
    alignSkillHighlightInView(container, item);
  }, [highlightedIndex, skills]);

  return (
    <div
      className="border-b border-[var(--color-border)] bg-[var(--color-bg-primary)]"
      role="listbox"
      aria-label="选择要强制开启的 Skill"
    >
      <div className="px-3 pb-1 pt-2 text-xs text-[var(--color-text-tertiary)]">
        输入 / 选择 Skill，最多 {MAX_FORCED_SKILLS} 个。继续输入可过滤。
      </div>
      <div ref={listRef} className="max-h-56 overflow-y-auto py-1">
        {loading ? (
          <div className="px-3 py-6 text-center text-xs text-[var(--color-text-tertiary)]">正在加载 Skills…</div>
        ) : skills.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-[var(--color-text-tertiary)]">
            {query ? `没有匹配 “/${query}” 的 Skill` : '当前没有可强制开启的 Skill。'}
          </div>
        ) : (
          skills.map((skill, index) => {
            const checked = selectedSet.has(skill.id);
            const itemDisabled = !checked && atLimit;
            const active = index === highlightedIndex;
            return (
              <button
                key={skill.id}
                ref={active ? highlightedRef : undefined}
                type="button"
                role="option"
                aria-selected={active}
                disabled={itemDisabled}
                className={`flex w-full flex-col items-start px-3 py-1.5 text-left disabled:cursor-not-allowed disabled:opacity-40 ${
                  active ? 'bg-[var(--color-bg-hover)]' : 'hover:bg-[var(--color-bg-hover)]'
                }`}
                onMouseEnter={() => onHighlight(index)}
                onMouseDown={(event) => {
                  event.preventDefault();
                  if (!itemDisabled) onPick(skill);
                }}
              >
                <span className="flex min-w-0 w-full items-center gap-2">
                  <span className="truncate font-mono text-sm text-[var(--color-text-primary)]">/{skill.name}</span>
                  {checked ? (
                    <span className="shrink-0 text-[10px] text-[var(--color-text-tertiary)]">已开启</span>
                  ) : null}
                </span>
                <span className="mt-0.5 w-full min-w-0 overflow-hidden break-words line-clamp-2 text-xs text-[var(--color-text-tertiary)]">
                  {skill.description || skill.trigger || skill.id}
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

export function ChatForcedSkillChips({
  skills,
  selectedIds,
  disabled,
  onRemove,
}: {
  skills: Skill[];
  selectedIds: string[];
  disabled?: boolean;
  onRemove: (skillId: string) => void;
}) {
  const selectedSet = new Set(selectedIds);
  const selectedSkills = skills.filter((skill) => selectedSet.has(skill.id));
  if (selectedSkills.length === 0) return null;
  return (
    <>
      {selectedSkills.map((skill) => (
        <span
          key={skill.id}
          className={COMPOSER_STATUS_CHIP_CLASS}
        >
          <BookOpen size={12} className="shrink-0" />
          <span className="truncate">/{skill.name}</span>
          <button
            type="button"
            className="icon-button flex h-4 w-4 items-center justify-center rounded-full text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-40"
            onClick={() => onRemove(skill.id)}
            disabled={disabled}
            aria-label={`取消强制开启 ${skill.name}`}
          >
            <X size={10} />
          </button>
        </span>
      ))}
    </>
  );
}
