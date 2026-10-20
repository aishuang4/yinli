/** Chat 发送框 / 菜单：常见指令 + Skill。 */

import { useEffect, useRef } from 'react';
import { BookOpen, ListChecks, Minimize2, Sparkles, Users, UserRound, X } from 'lucide-react';
import type { Skill } from '../../types/skill';
import { MAX_FORCED_SKILLS } from './forcedSkills';
import { COMPOSER_STATUS_CHIP_CLASS } from './plusMenu';
import type { ComposerSlashCommand, SlashListItem } from './slashCommands';

type ScrollBox = {
  scrollTop: number;
  getBoundingClientRect: () => Pick<DOMRect, 'top' | 'bottom'>;
};

type HighlightBox = {
  getBoundingClientRect: () => Pick<DOMRect, 'top' | 'bottom'>;
};

/** 只滚动 Skill 列表容器，避免 scrollIntoView 带动整页。 */
// eslint-disable-next-line react-refresh/only-export-components -- 列表滚动纯函数，测试与菜单共用
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

function commandIcon(command: ComposerSlashCommand) {
  if (command.id === 'compress') return <Minimize2 size={14} className="shrink-0" />;
  if (command.id === 'plan') return <ListChecks size={14} className="shrink-0" />;
  if (command.id === 'think') return <Sparkles size={14} className="shrink-0" />;
  if (command.id === 'team') return <Users size={14} className="shrink-0" />;
  return <UserRound size={14} className="shrink-0" />;
}

export function ChatSkillSlashMenu({
  items,
  selectedIds,
  highlightedIndex,
  loading = false,
  query,
  onHighlight,
  onPickCommand,
  onPickSkill,
}: {
  items: SlashListItem[];
  selectedIds: string[];
  highlightedIndex: number;
  loading?: boolean;
  query: string;
  onHighlight: (index: number) => void;
  onPickCommand: (command: ComposerSlashCommand) => void;
  onPickSkill: (skill: Skill) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const highlightedRef = useRef<HTMLButtonElement>(null);
  const selectedSet = new Set(selectedIds);
  const atLimit = selectedIds.length >= MAX_FORCED_SKILLS;
  const commandCount = items.filter((item) => item.kind === 'command').length;

  useEffect(() => {
    const container = listRef.current;
    const item = highlightedRef.current;
    if (!container || !item) return;
    alignSkillHighlightInView(container, item);
  }, [highlightedIndex, items]);

  return (
    <div
      className="border-b border-[var(--color-border)] bg-[var(--color-bg-primary)]"
      role="listbox"
      aria-label="选择斜杠指令或 Skill"
    >
      <div className="px-3 pb-1 pt-2 text-xs text-[var(--color-text-tertiary)]">
        输入 / 选择指令或 Skill。Skill 最多 {MAX_FORCED_SKILLS} 个。
      </div>
      <div ref={listRef} className="max-h-64 overflow-y-auto py-1">
        {loading && items.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-[var(--color-text-tertiary)]">正在加载…</div>
        ) : items.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-[var(--color-text-tertiary)]">
            {query ? `没有匹配 “/${query}” 的指令或 Skill` : '当前没有可选项。'}
          </div>
        ) : (
          items.map((item, index) => {
            const active = index === highlightedIndex;
            if (item.kind === 'command') {
              return (
                <button
                  key={`command:${item.command.id}`}
                  ref={active ? highlightedRef : undefined}
                  type="button"
                  role="option"
                  aria-selected={active}
                  className={`flex w-full items-start gap-2 px-3 py-1.5 text-left ${
                    active ? 'bg-[var(--color-bg-hover)]' : 'hover:bg-[var(--color-bg-hover)]'
                  }`}
                  onMouseEnter={() => onHighlight(index)}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    onPickCommand(item.command);
                  }}
                >
                  <span className="mt-0.5 text-[var(--color-text-secondary)]">{commandIcon(item.command)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate font-mono text-sm text-[var(--color-text-primary)]">/{item.command.token}</span>
                      <span className="truncate text-xs text-[var(--color-text-secondary)]">{item.command.title}</span>
                    </span>
                    <span className="mt-0.5 block w-full min-w-0 overflow-hidden break-words line-clamp-2 text-xs text-[var(--color-text-tertiary)]">
                      {item.command.description}
                    </span>
                  </span>
                </button>
              );
            }
            const checked = selectedSet.has(item.skill.id);
            const itemDisabled = !checked && atLimit;
            return (
              <button
                key={`skill:${item.skill.id}`}
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
                  if (!itemDisabled) onPickSkill(item.skill);
                }}
              >
                {index === commandCount && commandCount > 0 ? (
                  <span className="mb-1 text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">Skill</span>
                ) : null}
                <span className="flex min-w-0 w-full items-center gap-2">
                  <span className="truncate font-mono text-sm text-[var(--color-text-primary)]">/{(item.skill.loadName || item.skill.name)}</span>
                  {checked ? (
                    <span className="shrink-0 text-[10px] text-[var(--color-text-tertiary)]">已开启</span>
                  ) : null}
                </span>
                <span className="mt-0.5 w-full min-w-0 overflow-hidden break-words line-clamp-2 text-xs text-[var(--color-text-tertiary)]">
                  {item.skill.description || item.skill.trigger || item.skill.id}
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
          <span className="truncate">/{(skill.loadName || skill.name)}</span>
          <button
            type="button"
            className="icon-button flex h-4 w-4 items-center justify-center rounded-full text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-40"
            onClick={() => onRemove(skill.id)}
            disabled={disabled}
            aria-label={`取消强制开启 ${(skill.loadName || skill.name)}`}
          >
            <X size={10} />
          </button>
        </span>
      ))}
    </>
  );
}
