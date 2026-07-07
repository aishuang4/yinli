/**
 * MarkdownMemoryPanel — 用户、Agent 和全局 Markdown 记忆编辑面板。
 */

import { RefreshCw, Save } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@agentscope-ai/design';
import { SelectField, TextAreaField } from '../../components/form';
import type { Agent } from '../../types/agent';
import type { MarkdownMemoryDocument, MarkdownMemoryScope } from '../../types/memory';
import { MARKDOWN_TARGETS, type MarkdownTarget } from './memoryViewConfig';

interface MarkdownMemoryPanelProps {
  agents: Agent[];
  agentsLoading: boolean;
  agentOptions: Array<{ label: string; value: string }>;
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
  agentOptions,
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
    <section className="grid h-full min-h-0 grid-cols-[260px_minmax(0,1fr)] overflow-hidden">
      <aside className="border-r border-[var(--color-border)] p-5">
        <div className="space-y-2">
          {MARKDOWN_TARGETS.map((item) => {
            const Icon = item.icon;
            const active = target === item.key;
            return (
              <button
                key={item.key}
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
            );
          })}
        </div>
      </aside>

      <div className="grid min-h-0 grid-rows-[auto_1fr] overflow-hidden p-5">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <TargetIcon size={18} color="var(--color-accent)" />
              <h2 className="text-base font-semibold text-[var(--color-text-primary)]">{selectedTarget.label}</h2>
              <Tag size="small" color={doc?.exists ? 'success' : 'info'}>
                {doc?.exists ? '已创建' : '未创建'}
              </Tag>
            </div>
            <div className="mt-1 truncate text-xs text-[var(--color-text-tertiary)]">
              {doc?.path || 'memory file'}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {target === 'agent' && (
              <SelectField
                className="w-[220px]"
                value={selectedAgentId}
                loading={agentsLoading}
                options={agentOptions}
                onChange={(value) => setSelectedAgentId(String(value))}
                placeholder={agents.length ? '选择 Agent' : '暂无 Agent'}
              />
            )}
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
            <Spinner tip="加载记忆..." />
          </div>
        ) : (
          <TextAreaField
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="h-full min-h-0"
            fieldClassName="h-full min-h-0 resize-none"
            style={{
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
