/**
 * MarkdownMemoryPanel — 用户、Agent 和全局 Markdown 记忆编辑面板。
 */

import { RefreshCw, Save } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@/components/design';
import { TextAreaField } from '../../components/form';
import type { Agent } from '../../types/agent';
import type { MarkdownMemoryDocument, MarkdownMemoryScope } from '../../types/memory';
import { MARKDOWN_TARGETS, type MarkdownTarget } from './memoryViewConfig';

interface MarkdownMemoryPanelProps {
  agents: Agent[];
  agentsLoading: boolean;
  target: MarkdownMemoryScope;
  selectedTarget: MarkdownTarget;
  selectedAgent: Agent | null;
  selectedAgentId: string;
  setTarget: (target: MarkdownMemoryScope) => void;
  setSelectedAgentId: (id: string) => void;
  doc: MarkdownMemoryDocument | null;
  draft: string;
  setDraft: (value: string) => void;
  loading: boolean;
  saving: boolean;
  dirty: boolean;
  onSave: () => Promise<void>;
  onRefresh: () => Promise<void>;
}

export function MarkdownMemoryPanel({
  agents,
  agentsLoading,
  target,
  selectedTarget,
  selectedAgent,
  selectedAgentId,
  setTarget,
  setSelectedAgentId,
  doc,
  draft,
  setDraft,
  loading,
  saving,
  dirty,
  onSave,
  onRefresh,
}: MarkdownMemoryPanelProps) {
  const TargetIcon = selectedTarget.icon;

  return (
    <section className="grid h-full min-h-0 min-w-0 w-full grid-cols-[260px_minmax(0,1fr)] overflow-hidden">
      <aside className="min-h-0 min-w-0 overflow-y-auto border-r border-[var(--color-border)] p-5">
        <div className="space-y-2">
          {MARKDOWN_TARGETS.map((item) => {
            const Icon = item.icon;
            const active = target === item.key;
            return (
              <div key={item.key}>
                <button
                  type="button"
                  onClick={() => setTarget(item.key)}
                  className={[
                    'w-full rounded-md px-3 py-3 text-left transition-colors',
                    active
                      ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]'
                      : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
                  ].join(' ')}
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    <Icon size={15} />
                    {item.label}
                  </span>
                  <span className="mt-1 block text-xs opacity-80">{item.description}</span>
                </button>
                {item.key === 'agent' && active ? (
                  <div className="mt-1 space-y-0.5 pl-3" role="listbox" aria-label="选择 Agent 记忆">
                    {agentsLoading ? (
                      <p className="px-3 py-1 text-xs text-[var(--color-text-tertiary)]">正在加载 Agent…</p>
                    ) : agents.length === 0 ? (
                      <p className="px-3 py-1 text-xs text-[var(--color-text-tertiary)]">当前工作区还没有 Agent</p>
                    ) : agents.map((agent) => {
                      const selected = agent.id === selectedAgentId;
                      return (
                        <button
                          key={agent.id}
                          type="button"
                          role="option"
                          aria-selected={selected}
                          onClick={() => setSelectedAgentId(agent.id)}
                          className={[
                            'block w-full truncate rounded-md px-3 py-1.5 text-left text-sm',
                            selected
                              ? 'bg-[var(--color-bg-hover)] font-medium text-[var(--color-text-primary)]'
                              : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
                          ].join(' ')}
                        >
                          {agent.name || agent.id}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </aside>

      <div className="grid min-h-0 min-w-0 grid-rows-[auto_minmax(0,1fr)] overflow-hidden p-5">
        <div className="mb-4 flex min-w-0 flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <TargetIcon size={18} className="shrink-0" color="var(--color-accent)" />
              <h2 className="truncate text-base font-semibold text-[var(--color-text-primary)]">
                {target === 'agent' && selectedAgent
                  ? `${selectedTarget.label} · ${selectedAgent.name || selectedAgent.id}`
                  : selectedTarget.label}
              </h2>
              <Tag size="small" color={doc?.exists ? 'success' : 'info'}>
                {doc?.exists ? '已创建' : '未创建'}
              </Tag>
            </div>
            <div className="mt-1 truncate text-xs text-[var(--color-text-tertiary)]" title={doc?.path || undefined}>
              {doc?.path || 'memory file'}
            </div>
          </div>
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
            <Button icon={<RefreshCw size={14} />} onClick={() => void onRefresh()} loading={loading}>
              重新读取
            </Button>
            <Button type="primary" icon={<Save size={14} />} onClick={() => void onSave()} loading={saving} disabled={!dirty}>
              保存
            </Button>
          </div>
        </div>

        {target === 'agent' && !selectedAgent ? (
          <Empty type="noData" description="暂无 Agent 记忆" />
        ) : loading ? (
          <div className="flex justify-center py-12">
            <Spinner tip="加载记忆..."><div className="min-h-12 w-12" /></Spinner>
          </div>
        ) : (
          <TextAreaField
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="h-full min-h-0 min-w-0 w-full overflow-hidden"
            fieldClassName="h-full min-h-0 min-w-0 w-full max-w-full resize-none"
            style={{
              width: '100%',
              height: '100%',
              fontFamily: 'var(--font-mono)',
              fontSize: 13,
              lineHeight: 1.6,
            }}
          />
        )}
      </div>
    </section>
  );
}
