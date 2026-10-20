/** Chat 发送框 + 菜单：左侧分类，右侧子清单。 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import {
  BookOpen,
  ChevronRight,
  ImagePlus,
  ListChecks,
  Minimize2,
  Paperclip,
  Plus,
  Search,
  Sparkles,
  WandSparkles,
  X,
} from 'lucide-react';
import {
  SparkDeepThinkLine,
  SparkReinforcementLearningLine,
  SparkRenyizhuanrenyiAnyToanyLine,
} from '@agentscope-ai/icons';
import type { Skill } from '../../types/skill';
import { ChatAgentAvatar, type ComposerAgentOption } from './ChatAgentSelect';
import { alignSkillHighlightInView } from './ChatSkillPicker';
import { MAX_FORCED_SKILLS } from './forcedSkills';
import {
  buildPlusMenuCategories,
  buildPlusMenuItems,
  COMPOSER_STATUS_CHIP_CLASS,
  computePlusMenuPrimaryPosition,
  computePlusMenuSubmenuTop,
  PLUS_MENU_CATEGORY_HOVER_DELAY_MS,
  PLUS_MENU_CATEGORY_LABELS,
  PLUS_MENU_LEFT_WIDTH,
  PLUS_MENU_RIGHT_WIDTH,
  PLUS_MENU_SEARCH_PLACEHOLDERS,
  PLUS_MENU_VIEWPORT_PAD,
  plusMenuItemKey,
  type PlusMenuCategory,
  type PlusMenuItem,
} from './plusMenu';

export function ChatPlanChip({
  disabled,
  onRemove,
}: {
  disabled?: boolean;
  onRemove: () => void;
}) {
  return (
    <span className={COMPOSER_STATUS_CHIP_CLASS}>
      <ListChecks size={12} className="shrink-0" />
      <span>计划</span>
      <button
        type="button"
        className="icon-button flex h-4 w-4 items-center justify-center rounded-full text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-40"
        onClick={onRemove}
        disabled={disabled}
        aria-label="关闭计划模式"
      >
        <X size={10} />
      </button>
    </span>
  );
}

export function ChatCompressChip({
  disabled,
  onRemove,
}: {
  disabled?: boolean;
  onRemove: () => void;
}) {
  return (
    <span className={COMPOSER_STATUS_CHIP_CLASS}>
      <Minimize2 size={12} className="shrink-0" />
      <span>压缩上下文</span>
      <button
        type="button"
        className="icon-button flex h-4 w-4 items-center justify-center rounded-full text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-40"
        onClick={onRemove}
        disabled={disabled}
        aria-label="取消压缩上下文"
      >
        <X size={10} />
      </button>
    </span>
  );
}

export function ChatDeepThinkChip({
  disabled,
  onRemove,
}: {
  disabled?: boolean;
  onRemove: () => void;
}) {
  return (
    <span className={COMPOSER_STATUS_CHIP_CLASS}>
      <SparkDeepThinkLine size={12} className="shrink-0" />
      <span>深度思考</span>
      <button
        type="button"
        className="icon-button flex h-4 w-4 items-center justify-center rounded-full text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-40"
        onClick={onRemove}
        disabled={disabled}
        aria-label="关闭深度思考"
      >
        <X size={10} />
      </button>
    </span>
  );
}

type ChatComposerPlusMenuProps = {
  open: boolean;
  category: PlusMenuCategory;
  onOpenChange: (open: boolean) => void;
  onCategoryChange: (category: PlusMenuCategory) => void;
  retainRef?: RefObject<HTMLDivElement | null>;
  disabled?: boolean;
  planMode: boolean;
  deepThinking: boolean;
  targetMode: 'agent' | 'team';
  showPlan: boolean;
  showTeam: boolean;
  agents: ComposerAgentOption[];
  selectedAgentId: string;
  agentsLoading?: boolean;
  skills: Skill[];
  selectedSkillIds: string[];
  skillsEnabled: boolean;
  skillsLoading?: boolean;
  imageDisabled: boolean;
  fileDisabled: boolean;
  onTogglePlan: () => void;
  onToggleDeepThinking: () => void;
  onTargetModeChange: (mode: 'agent' | 'team') => void;
  onPickAgent: (agentId: string) => void;
  onPickSkill: (skill: Skill) => void;
  onUploadImage: () => void;
  onUploadFile: () => void;
};

export function ChatComposerPlusMenu({
  open,
  category,
  onOpenChange,
  onCategoryChange,
  retainRef,
  disabled = false,
  planMode,
  deepThinking,
  targetMode,
  showPlan,
  showTeam,
  agents,
  selectedAgentId,
  agentsLoading = false,
  skills,
  selectedSkillIds,
  skillsEnabled,
  skillsLoading = false,
  imageDisabled,
  fileDisabled,
  onTogglePlan,
  onToggleDeepThinking,
  onTargetModeChange,
  onPickAgent,
  onPickSkill,
  onUploadImage,
  onUploadFile,
}: ChatComposerPlusMenuProps) {
  const [query, setQuery] = useState('');
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [anchorRect, setAnchorRect] = useState<{ left: number; top: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const categoryRef = useRef<HTMLDivElement>(null);
  const submenuRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const highlightedRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const categories = useMemo(
    () => buildPlusMenuCategories({ skillsEnabled }),
    [skillsEnabled],
  );
  const items = useMemo(
    () => buildPlusMenuItems({
      category,
      query,
      showPlan,
      showTeam,
      skillsEnabled,
      skills,
      agents,
    }),
    [agents, category, query, showPlan, showTeam, skills, skillsEnabled],
  );
  useEffect(() => {
    if (disabled && open) onOpenChange(false);
  }, [disabled, open, onOpenChange]);

  useEffect(() => {
    if (!categories.includes(category)) {
      onCategoryChange(categories[0] ?? 'files');
    }
  }, [categories, category, onCategoryChange]);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setHighlightedIndex(0);
    const frame = requestAnimationFrame(() => searchRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open, category]);

  useLayoutEffect(() => {
    if (!open) return;
    const updateAnchor = () => {
      const rect = buttonRef.current?.getBoundingClientRect();
      if (!rect) return;
      setAnchorRect((prev) => (
        prev && prev.left === rect.left && prev.top === rect.top
          ? prev
          : { left: rect.left, top: rect.top }
      ));
    };
    updateAnchor();
    window.addEventListener('resize', updateAnchor);
    window.addEventListener('scroll', updateAnchor, true);
    return () => {
      window.removeEventListener('resize', updateAnchor);
      window.removeEventListener('scroll', updateAnchor, true);
    };
  }, [open]);

  useEffect(() => {
    setHighlightedIndex(0);
  }, [query]);

  useEffect(() => {
    if (highlightedIndex >= items.length) {
      setHighlightedIndex(0);
    }
  }, [highlightedIndex, items.length]);

  useEffect(() => {
    if (!open) return;
    const item = highlightedRef.current;
    const container = listRef.current;
    if (!container || !item) return;
    alignSkillHighlightInView(container, item);
  }, [highlightedIndex, items, open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        buttonRef.current?.contains(target)
        || categoryRef.current?.contains(target)
        || submenuRef.current?.contains(target)
        || retainRef?.current?.contains(target)
      ) {
        return;
      }
      onOpenChange(false);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onOpenChange(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onOpenChange, open, retainRef]);

  const closeThen = (action: () => void) => {
    onOpenChange(false);
    requestAnimationFrame(action);
  };

  const activate = (item: PlusMenuItem | undefined) => {
    if (!item) return;
    if (item.kind === 'plan') {
      onTogglePlan();
      return;
    }
    if (item.kind === 'think') {
      onToggleDeepThinking();
      return;
    }
    if (item.kind === 'agent-mode') {
      onTargetModeChange('agent');
      return;
    }
    if (item.kind === 'team-mode') {
      onTargetModeChange('team');
      return;
    }
    if (item.kind === 'skill') {
      if (!selectedSkillIds.includes(item.skill.id) && selectedSkillIds.length >= MAX_FORCED_SKILLS) return;
      onPickSkill(item.skill);
      return;
    }
    if (item.kind === 'agent') {
      if (targetMode === 'team') return;
      closeThen(() => onPickAgent(item.agent.id));
      return;
    }
    if (item.kind === 'image') {
      if (imageDisabled) return;
      closeThen(onUploadImage);
      return;
    }
    if (fileDisabled) return;
    closeThen(onUploadFile);
  };

  const moveCategory = (delta: number) => {
    const index = categories.indexOf(category);
    const next = categories[(index + delta + categories.length) % categories.length];
    if (next) onCategoryChange(next);
  };

  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setHighlightedIndex((index) => (items.length === 0 ? 0 : (index + 1) % items.length));
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlightedIndex((index) => (items.length === 0 ? 0 : (index - 1 + items.length) % items.length));
      return;
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      moveCategory(1);
      return;
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      moveCategory(-1);
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      activate(items[highlightedIndex]);
    }
  };

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className={`icon-button h-8 w-8 rounded-[10px] text-[var(--color-text-secondary)] transition-colors duration-200 hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-40 ${
          open ? 'bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]' : ''
        }`}
        onClick={() => {
          if (!open) {
            const rect = buttonRef.current?.getBoundingClientRect();
            if (rect) setAnchorRect({ left: rect.left, top: rect.top });
          }
          onOpenChange(!open);
        }}
        disabled={disabled}
        aria-label="添加文件、模式、Agent 或技能"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {open ? <X size={16} /> : <Plus size={16} />}
      </button>
      {open ? createPortal(
        <PlusMenuFlyout
          categoryRef={categoryRef}
          submenuRef={submenuRef}
          listRef={listRef}
          highlightedRef={highlightedRef}
          searchRef={searchRef}
          anchorRect={anchorRect}
          categories={categories}
          category={category}
          query={query}
          items={items}
          highlightedIndex={highlightedIndex}
          selectedSkillIds={selectedSkillIds}
          selectedAgentId={selectedAgentId}
          planMode={planMode}
          deepThinking={deepThinking}
          targetMode={targetMode}
          skillsLoading={skillsLoading}
          agentsLoading={agentsLoading}
          imageDisabled={imageDisabled}
          fileDisabled={fileDisabled}
          onCategoryChange={onCategoryChange}
          onQueryChange={setQuery}
          onHighlight={setHighlightedIndex}
          onSearchKeyDown={handleSearchKeyDown}
          onActivate={activate}
        />,
        document.body,
      ) : null}
    </>
  );
}

function PlusMenuFlyout({
  categoryRef,
  submenuRef,
  listRef,
  highlightedRef,
  searchRef,
  anchorRect,
  categories,
  category,
  query,
  items,
  highlightedIndex,
  selectedSkillIds,
  selectedAgentId,
  planMode,
  deepThinking,
  targetMode,
  skillsLoading,
  agentsLoading,
  imageDisabled,
  fileDisabled,
  onCategoryChange,
  onQueryChange,
  onHighlight,
  onSearchKeyDown,
  onActivate,
}: {
  categoryRef: { current: HTMLDivElement | null };
  submenuRef: { current: HTMLDivElement | null };
  listRef: { current: HTMLDivElement | null };
  highlightedRef: { current: HTMLButtonElement | null };
  searchRef: { current: HTMLInputElement | null };
  anchorRect: { left: number; top: number } | null;
  categories: PlusMenuCategory[];
  category: PlusMenuCategory;
  query: string;
  items: PlusMenuItem[];
  highlightedIndex: number;
  selectedSkillIds: string[];
  selectedAgentId: string;
  planMode: boolean;
  deepThinking: boolean;
  targetMode: 'agent' | 'team';
  skillsLoading: boolean;
  agentsLoading: boolean;
  imageDisabled: boolean;
  fileDisabled: boolean;
  onCategoryChange: (category: PlusMenuCategory) => void;
  onQueryChange: (value: string) => void;
  onHighlight: (index: number) => void;
  onSearchKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void;
  onActivate: (item: PlusMenuItem) => void;
}) {
  const selectedSet = new Set(selectedSkillIds);
  const atLimit = selectedSkillIds.length >= MAX_FORCED_SKILLS;
  const categoryButtonRefs = useRef(new Map<PlusMenuCategory, HTMLButtonElement | null>());
  const hoverTimerRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  const [submenuTop, setSubmenuTop] = useState(PLUS_MENU_VIEWPORT_PAD);
  const panelClass = 'fixed z-[70] overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-primary)] shadow-[0_18px_44px_rgba(0,0,0,0.18)]';
  const primary = computePlusMenuPrimaryPosition({
    anchorLeft: anchorRect?.left ?? PLUS_MENU_VIEWPORT_PAD,
    anchorTop: anchorRect?.top ?? window.innerHeight - 24,
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
  });

  useLayoutEffect(() => {
    const update = () => {
      const itemEl = categoryButtonRefs.current.get(category);
      const submenuEl = submenuRef.current;
      if (!itemEl || !submenuEl) return;
      const next = computePlusMenuSubmenuTop({
        categoryItemTop: itemEl.getBoundingClientRect().top,
        submenuHeight: submenuEl.offsetHeight,
        viewportHeight: window.innerHeight,
      });
      setSubmenuTop((prev) => (prev === next ? prev : next));
    };
    update();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    if (submenuRef.current) observer?.observe(submenuRef.current);
    return () => observer?.disconnect();
  }, [anchorRect, category, items, query, skillsLoading, agentsLoading, submenuRef, targetMode, primary.primaryBottom, primary.left]);

  useEffect(() => () => {
    if (hoverTimerRef.current != null) window.clearTimeout(hoverTimerRef.current);
  }, []);

  const clearHoverTimer = () => {
    if (hoverTimerRef.current == null) return;
    window.clearTimeout(hoverTimerRef.current);
    hoverTimerRef.current = null;
  };

  const commitCategory = (next: PlusMenuCategory) => {
    clearHoverTimer();
    if (next !== category) onCategoryChange(next);
  };

  const previewCategory = (next: PlusMenuCategory) => {
    if (next === category) {
      clearHoverTimer();
      return;
    }
    clearHoverTimer();
    hoverTimerRef.current = window.setTimeout(() => {
      hoverTimerRef.current = null;
      onCategoryChange(next);
    }, PLUS_MENU_CATEGORY_HOVER_DELAY_MS);
  };

  return (
    <>
      <div
        ref={categoryRef}
        className={panelClass}
        style={{ left: primary.left, bottom: primary.primaryBottom, width: PLUS_MENU_LEFT_WIDTH }}
        role="menu"
        aria-label="发送框菜单分类"
      >
        <div className="p-1.5">
          {categories.map((item) => {
            const active = item === category;
            return (
              <button
                key={item}
                ref={(node) => {
                  if (node) categoryButtonRefs.current.set(item, node);
                  else categoryButtonRefs.current.delete(item);
                }}
                type="button"
                role="menuitem"
                aria-haspopup="true"
                aria-expanded={active}
                className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm ${
                  active
                    ? 'bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]'
                    : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
                }`}
                onMouseEnter={() => previewCategory(item)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => commitCategory(item)}
              >
                <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                  {categoryIcon(item)}
                </span>
                <span className="min-w-0 flex-1">{PLUS_MENU_CATEGORY_LABELS[item]}</span>
                <ChevronRight size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
              </button>
            );
          })}
        </div>
      </div>
      <div
        ref={submenuRef}
        className={panelClass}
        style={{ left: primary.rightLeft, top: submenuTop, width: PLUS_MENU_RIGHT_WIDTH }}
        role="listbox"
        aria-label={PLUS_MENU_CATEGORY_LABELS[category]}
        onMouseEnter={clearHoverTimer}
      >
        <label className="flex items-center gap-2 px-3 py-2.5">
          <Search size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={onSearchKeyDown}
            placeholder={PLUS_MENU_SEARCH_PLACEHOLDERS[category]}
            className="block w-full border-0 bg-transparent text-sm text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)]"
            aria-label={PLUS_MENU_SEARCH_PLACEHOLDERS[category]}
          />
        </label>
        {category === 'agents' && targetMode === 'team' ? (
          <div className="px-3 pb-2 text-xs text-[var(--color-text-tertiary)]">
            Team 模式沿用当前 Agent 作为 Leader
          </div>
        ) : null}
        <div ref={listRef} className="max-h-72 overflow-y-auto px-1 pb-1">
          {emptyCopy({
            category,
            items,
            query,
            skillsLoading,
            agentsLoading,
          }) ? (
            <div className="px-2 py-6 text-center text-xs text-[var(--color-text-tertiary)]">
              {emptyCopy({ category, items, query, skillsLoading, agentsLoading })}
            </div>
          ) : (
            items.map((item, index) => {
              const active = index === highlightedIndex;
              return (
                <PlusMenuRow
                  key={plusMenuItemKey(item)}
                  item={item}
                  active={active}
                  planMode={planMode}
                  deepThinking={deepThinking}
                  targetMode={targetMode}
                  selectedAgentId={selectedAgentId}
                  skillChecked={item.kind === 'skill' && selectedSet.has(item.skill.id)}
                  skillDisabled={item.kind === 'skill' && !selectedSet.has(item.skill.id) && atLimit}
                  imageDisabled={imageDisabled}
                  fileDisabled={fileDisabled}
                  agentDisabled={item.kind === 'agent' && targetMode === 'team'}
                  buttonRef={active ? highlightedRef : undefined}
                  onHover={() => onHighlight(index)}
                  onPick={() => onActivate(item)}
                />
              );
            })
          )}
        </div>
      </div>
    </>
  );
}

function PlusMenuRow({
  item,
  active,
  planMode,
  deepThinking,
  targetMode,
  selectedAgentId,
  skillChecked,
  skillDisabled,
  imageDisabled,
  fileDisabled,
  agentDisabled,
  buttonRef,
  onHover,
  onPick,
}: {
  item: PlusMenuItem;
  active: boolean;
  planMode: boolean;
  deepThinking: boolean;
  targetMode: 'agent' | 'team';
  selectedAgentId: string;
  skillChecked: boolean;
  skillDisabled: boolean;
  imageDisabled: boolean;
  fileDisabled: boolean;
  agentDisabled: boolean;
  buttonRef?: { current: HTMLButtonElement | null };
  onHover: () => void;
  onPick: () => void;
}) {
  const disabled = (item.kind === 'skill' && skillDisabled)
    || (item.kind === 'image' && imageDisabled)
    || (item.kind === 'file' && fileDisabled)
    || (item.kind === 'agent' && agentDisabled);
  const visual = describeItem(item, {
    planMode,
    deepThinking,
    targetMode,
    selectedAgentId,
    skillChecked,
  });
  return (
    <button
      ref={buttonRef}
      type="button"
      role="option"
      aria-selected={active}
      disabled={disabled}
      className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left disabled:cursor-not-allowed disabled:opacity-40 ${
        active ? 'bg-[var(--color-bg-hover)]' : 'hover:bg-[var(--color-bg-hover)]'
      }`}
      onMouseEnter={onHover}
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => {
        if (!disabled) onPick();
      }}
    >
      <span className="flex h-5 w-5 shrink-0 items-center justify-center text-[var(--color-text-secondary)]">
        {visual.icon}
      </span>
      <span className="min-w-0 flex-1 truncate text-sm text-[var(--color-text-primary)]">{visual.title}</span>
      {visual.checked ? (
        <span className="shrink-0 text-[10px] text-[var(--color-text-tertiary)]">{visual.badge}</span>
      ) : null}
    </button>
  );
}

function describeItem(
  item: PlusMenuItem,
  state: {
    planMode: boolean;
    deepThinking: boolean;
    targetMode: 'agent' | 'team';
    selectedAgentId: string;
    skillChecked: boolean;
  },
): { icon: ReactNode; title: string; checked: boolean; badge: string } {
  if (item.kind === 'plan') {
    return { icon: <ListChecks size={15} />, title: '计划模式', checked: state.planMode, badge: '已开启' };
  }
  if (item.kind === 'think') {
    return { icon: <SparkDeepThinkLine size={15} />, title: '深度思考', checked: state.deepThinking, badge: '已开启' };
  }
  if (item.kind === 'agent-mode') {
    return {
      icon: <SparkReinforcementLearningLine size={15} />,
      title: '单 Agent',
      checked: state.targetMode === 'agent',
      badge: '当前',
    };
  }
  if (item.kind === 'team-mode') {
    return {
      icon: <SparkRenyizhuanrenyiAnyToanyLine size={15} />,
      title: 'Team',
      checked: state.targetMode === 'team',
      badge: '当前',
    };
  }
  if (item.kind === 'skill') {
    return {
      icon: <BookOpen size={15} />,
      title: `/${item.skill.name}`,
      checked: state.skillChecked,
      badge: '已开启',
    };
  }
  if (item.kind === 'agent') {
    return {
      icon: <ChatAgentAvatar agent={item.agent} size={20} />,
      title: item.agent.name,
      checked: item.agent.id === state.selectedAgentId,
      badge: '当前',
    };
  }
  if (item.kind === 'image') {
    return { icon: <ImagePlus size={15} />, title: '上传图片', checked: false, badge: '' };
  }
  return { icon: <Paperclip size={15} />, title: '上传文件', checked: false, badge: '' };
}

function categoryIcon(category: PlusMenuCategory): ReactNode {
  if (category === 'files') return <Paperclip size={15} />;
  if (category === 'modes') return <Sparkles size={15} />;
  if (category === 'agents') return <SparkReinforcementLearningLine size={15} />;
  return <WandSparkles size={15} />;
}

function emptyCopy({
  category,
  items,
  query,
  skillsLoading,
  agentsLoading,
}: {
  category: PlusMenuCategory;
  items: PlusMenuItem[];
  query: string;
  skillsLoading: boolean;
  agentsLoading: boolean;
}): string {
  if (category === 'skills' && skillsLoading && items.length === 0) return '正在加载 Skills…';
  if (category === 'agents' && agentsLoading && items.length === 0) return '正在加载 Agent…';
  if (items.length === 0) return query ? '没有匹配项' : '当前没有可选项';
  return '';
}

