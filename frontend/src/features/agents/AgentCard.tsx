/**
 * AgentCard — Agent 列表卡片组件
 *
 * 在 Agent 列表中展示单个 Agent 的摘要信息。
 * 点击选中，显示删除按钮。
 *
 * @see vercel-composition-patterns: patterns-explicit-variants
 */

import { Bot, Trash2 } from 'lucide-react';
import type { Agent } from '../../types/agent';
import { cn } from '../../utils/cn';

interface AgentCardProps {
  /** Agent 数据 */
  agent: Agent;
  /** 是否选中 */
  selected: boolean;
  /** 点击回调 */
  onSelect: () => void;
  /** 删除回调 */
  onDelete: () => void;
}

/**
 * AgentCard 组件
 */
export function AgentCard({ agent, selected, onSelect, onDelete }: AgentCardProps) {
  return (
    <div
      onClick={onSelect}
      className={cn(
        'group relative',
        'p-4 rounded-lg border cursor-pointer transition-all',
        'hover:bg-[var(--color-bg-hover)]',
        selected
          ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg)]'
          : 'border-[var(--color-border)] bg-[var(--color-bg-primary)]'
      )}
    >
      {/* 头像 + 名称 */}
      <div className="flex items-center gap-3 mb-2">
        <div
          className={cn(
            'w-10 h-10 rounded-full flex items-center justify-center',
            'bg-[var(--color-agent-leader)] text-white'
          )}
        >
          {agent.avatar ? (
            <img src={agent.avatar} alt={agent.name} className="w-full h-full rounded-full object-cover" />
          ) : (
            <Bot size={20} strokeWidth={1.5} />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-medium text-[var(--color-text-primary)] truncate">
            {agent.name}
          </div>
          <div className="text-xs text-[var(--color-text-secondary)] truncate">
            {agent.modelConfig.primary}
          </div>
        </div>

        {/* 删除按钮 */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className={cn(
            'p-1.5 rounded-md transition-opacity',
            'hover:bg-[var(--color-error-bg)] hover:text-[var(--color-error)]',
            'opacity-0 group-hover:opacity-100'
          )}
          aria-label="删除 Agent"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {/* 描述 */}
      {agent.description && (
        <div className="text-sm text-[var(--color-text-secondary)] line-clamp-2 mb-2">
          {agent.description}
        </div>
      )}

      {/* 标签 */}
      {agent.tags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {agent.tags.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="px-2 py-0.5 text-xs rounded-full bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)]"
            >
              {tag}
            </span>
          ))}
          {agent.tags.length > 3 && (
            <span className="px-2 py-0.5 text-xs text-[var(--color-text-tertiary)]">
              +{agent.tags.length - 3}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
