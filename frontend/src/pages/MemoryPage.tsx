/**
 * MemoryPage — 记忆管理页面
 *
 * 管理唯一用户记忆、唯一全局记忆、每个 Agent 的 Markdown 记忆，以及历史记忆检索。
 */

import { useEffect, useMemo, useState } from 'react';
import { Bot, Brain, Database, FileText, Globe2, RefreshCw, Save, UserRound } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@agentscope-ai/design';
import { NodeMemoryFilters } from '../features/memory/NodeMemoryFilters';
import { SelectField, TextAreaField } from '../components/form';
import { useAgents } from '../hooks/useAgents';
import { useMemory } from '../hooks/useMemory';
import type { Agent } from '../types/agent';
import type { MarkdownMemoryDocument, MarkdownMemoryScope, MemoryNodeQueryResult, MemoryNodeTreeItem } from '../types/memory';

type MemoryTab = 'markdown' | 'nodes';

interface MarkdownTarget {
  key: MarkdownMemoryScope;
  label: string;
  description: string;
  icon: typeof UserRound;
}

const MARKDOWN_TARGETS: MarkdownTarget[] = [
  { key: 'user', label: '用户记忆', description: '唯一用户长期记忆', icon: UserRound },
  { key: 'agent', label: 'Agent 记忆', description: '按 Agent 独立保存', icon: Bot },
  { key: 'global', label: '全局记忆', description: '唯一全局共享规则', icon: Globe2 },
];

const MEMORY_TABS: Array<{ key: MemoryTab; label: string; icon: typeof FileText }> = [
  { key: 'markdown', label: 'Markdown 记忆', icon: FileText },
  { key: 'nodes', label: '历史记忆', icon: Database },
];

export function MemoryPage() {
  const { agents, loading: agentsLoading, refresh: refreshAgents } = useAgents();
  const { readMarkdownMemory, saveMarkdownMemory, loadNodeTree, queryNodes } = useMemory();
  const [activeTab, setActiveTab] = useState<MemoryTab>('markdown');
  const [target, setTarget] = useState<MarkdownMemoryScope>('user');
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [doc, setDoc] = useState<MarkdownMemoryDocument | null>(null);
  const [draft, setDraft] = useState('');
  const [markdownLoading, setMarkdownLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [nodeAgentId, setNodeAgentId] = useState('');
  const [nodeQuery, setNodeQuery] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [nodeTree, setNodeTree] = useState<MemoryNodeTreeItem[]>([]);
  const [nodeResults, setNodeResults] = useState<MemoryNodeQueryResult[]>([]);
  const [nodesLoading, setNodesLoading] = useState(false);
  const [error, setError] = useState('');

  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId) || agents[0] || null;
  const agentOptions = useMemo(() => agents.map((agent) => ({
    label: agent.name,
    value: agent.id,
  })), [agents]);
  const selectedTarget = MARKDOWN_TARGETS.find((item) => item.key === target) || MARKDOWN_TARGETS[0];
  const markdownDirty = doc ? draft !== doc.content : false;

  useEffect(() => {
    if (!selectedAgentId && agents[0]) {
      setSelectedAgentId(agents[0].id);
    }
  }, [agents, selectedAgentId]);

  useEffect(() => {
    if (target !== 'agent' || selectedAgent) {
      void loadMarkdown();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, selectedAgentId]);

  useEffect(() => {
    void loadNodes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadMarkdown() {
    try {
      setMarkdownLoading(true);
      setError('');
      const next = await readMarkdownMemory(target, target === 'agent' ? selectedAgent?.id : undefined);
      setDoc(next);
      setDraft(next.content);
    } catch (err) {
      setError(err instanceof Error ? err.message : '记忆加载失败');
    } finally {
      setMarkdownLoading(false);
    }
  }

  async function saveMarkdown() {
    try {
      setSaving(true);
      setError('');
      const next = await saveMarkdownMemory(target, draft, target === 'agent' ? selectedAgent?.id : undefined);
      setDoc(next);
      setDraft(next.content);
    } catch (err) {
      setError(err instanceof Error ? err.message : '记忆保存失败');
    } finally {
      setSaving(false);
    }
  }

  async function loadNodes() {
    try {
      setNodesLoading(true);
      setError('');
      const filters = {
        query: nodeQuery.trim() || undefined,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
      };
      const [tree, results] = await Promise.all([
        loadNodeTree({ ...filters, limit: 200 }),
        queryNodes({ ...filters, limit: 100 }),
      ]);
      setNodeTree(tree);
      setNodeResults(results);
    } catch (err) {
      setError(err instanceof Error ? err.message : '历史记忆加载失败');
    } finally {
      setNodesLoading(false);
    }
  }

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_1fr] overflow-hidden bg-[var(--color-bg-primary)]">
      <header className="border-b border-[var(--color-border)] px-5 py-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Brain size={20} color="var(--color-accent)" />
              <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">Memory</h1>
            </div>
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              管理用户、Agent、全局长期记忆和历史记忆。
            </p>
          </div>
          <Button icon={<RefreshCw size={14} />} onClick={() => {
            void refreshAgents();
            if (activeTab === 'markdown') void loadMarkdown();
            if (activeTab === 'nodes') void loadNodes();
          }}>
            刷新
          </Button>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3">
          {MEMORY_TABS.map((item) => {
            const Icon = item.icon;
            const active = activeTab === item.key;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setActiveTab(item.key)}
                className={[
                  'rounded-md px-4 py-3 text-left transition-colors',
                  active
                    ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]'
                    : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
                ].join(' ')}
              >
                <span className="flex items-center gap-2 text-sm font-medium">
                  <Icon size={15} />
                  {item.label}
                </span>
              </button>
            );
          })}
        </div>
      </header>

      <main className="min-h-0 overflow-hidden">
        {error && (
          <div className="mx-5 mt-4 rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] p-3 text-sm text-[var(--color-error)]">
            {error}
          </div>
        )}
        {activeTab === 'markdown' ? (
          <MarkdownMemoryPanel
            agents={agents}
            agentsLoading={agentsLoading}
            agentOptions={agentOptions}
            target={target}
            selectedTarget={selectedTarget}
            selectedAgent={selectedAgent}
            selectedAgentId={selectedAgentId}
            setTarget={setTarget}
            setSelectedAgentId={setSelectedAgentId}
            doc={doc}
            draft={draft}
            setDraft={setDraft}
            loading={markdownLoading}
            saving={saving}
            dirty={markdownDirty}
            onSave={saveMarkdown}
            onRefresh={loadMarkdown}
          />
        ) : (
          <NodeMemoryPanel
            agents={agents}
            nodeAgentId={nodeAgentId}
            setNodeAgentId={setNodeAgentId}
            nodeQuery={nodeQuery}
            setNodeQuery={setNodeQuery}
            dateFrom={dateFrom}
            setDateFrom={setDateFrom}
            dateTo={dateTo}
            setDateTo={setDateTo}
            loading={nodesLoading}
            nodeTree={nodeTree}
            nodeResults={nodeResults}
            onSearch={loadNodes}
          />
        )}
      </main>
    </div>
  );
}

function MarkdownMemoryPanel({
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
}: {
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
}) {
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

function NodeMemoryPanel({
  agents,
  nodeAgentId,
  setNodeAgentId,
  nodeQuery,
  setNodeQuery,
  dateFrom,
  setDateFrom,
  dateTo,
  setDateTo,
  loading,
  nodeTree,
  nodeResults,
  onSearch,
}: {
  agents: Agent[];
  nodeAgentId: string;
  setNodeAgentId: (value: string) => void;
  nodeQuery: string;
  setNodeQuery: (value: string) => void;
  dateFrom: string;
  setDateFrom: (value: string) => void;
  dateTo: string;
  setDateTo: (value: string) => void;
  loading: boolean;
  nodeTree: MemoryNodeTreeItem[];
  nodeResults: MemoryNodeQueryResult[];
  onSearch: () => Promise<void>;
}) {
  const agentNameById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent.name])), [agents]);
  const [selectedHistoryId, setSelectedHistoryId] = useState('');
  const agentCounts = useMemo(() => {
    const counts = new Map<string, number>();
    nodeTree.forEach((node) => counts.set(node.agentId, (counts.get(node.agentId) || 0) + 1));
    return counts;
  }, [nodeTree]);
  const visibleTree = useMemo(() => (
    nodeAgentId ? nodeTree.filter((node) => node.agentId === nodeAgentId) : nodeTree
  ), [nodeAgentId, nodeTree]);
  const visibleResults = useMemo(() => (
    nodeAgentId ? nodeResults.filter((node) => node.agentId === nodeAgentId) : nodeResults
  ), [nodeAgentId, nodeResults]);
  const groupedTimeline = useMemo(() => {
    const groups = new Map<string, MemoryNodeTreeItem[]>();
    visibleTree.forEach((node) => {
      const list = groups.get(node.date) || [];
      list.push(node);
      groups.set(node.date, list);
    });
    return Array.from(groups.entries());
  }, [visibleTree]);
  const selectedTimelineItem = useMemo(() => (
    visibleTree.find((node) => node.nodeId === selectedHistoryId) || visibleTree[0] || null
  ), [selectedHistoryId, visibleTree]);
  const selectedHistory = useMemo(() => (
    visibleResults.find((node) => node.nodeId === selectedTimelineItem?.nodeId) || null
  ), [selectedTimelineItem?.nodeId, visibleResults]);

  useEffect(() => {
    if (!visibleTree.length) {
      setSelectedHistoryId('');
      return;
    }
    if (!visibleTree.some((node) => node.nodeId === selectedHistoryId)) {
      setSelectedHistoryId(visibleTree[0].nodeId);
    }
  }, [selectedHistoryId, visibleTree]);

  return (
    <section className="grid h-full min-h-0 grid-rows-[auto_1fr] overflow-hidden p-5">
      <NodeMemoryFilters
        query={nodeQuery}
        setQuery={setNodeQuery}
        dateFrom={dateFrom}
        setDateFrom={setDateFrom}
        dateTo={dateTo}
        setDateTo={setDateTo}
        loading={loading}
        onSearch={() => void onSearch()}
      />

      <div className="mt-5 grid min-h-0 grid-cols-[260px_360px_minmax(0,1fr)] gap-5 overflow-hidden">
        <div className="min-h-0 overflow-y-auto rounded-md border border-[var(--color-border)]">
          <div className="border-b border-[var(--color-border)] px-4 py-3 text-sm font-medium text-[var(--color-text-primary)]">
            Agent
          </div>
          <div className="p-3">
            <button
              type="button"
              onClick={() => setNodeAgentId('')}
              className={[
                'mb-2 w-full rounded-md px-3 py-3 text-left transition-colors',
                nodeAgentId === ''
                  ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]'
                  : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
              ].join(' ')}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
                  <Brain size={15} />
                  <span className="truncate">全部 Agent</span>
                </span>
                <Tag size="small" color="blue">{nodeTree.length}</Tag>
              </div>
            </button>
            {agents.length === 0 ? (
              <Empty type="noData" description="暂无 Agent" />
            ) : (
              <div className="space-y-2">
                {agents.map((agent) => {
                  const active = nodeAgentId === agent.id;
                  const count = agentCounts.get(agent.id) || 0;
                  return (
                    <button
                      key={agent.id}
                      type="button"
                      onClick={() => setNodeAgentId(agent.id)}
                      className={[
                        'w-full rounded-md px-3 py-3 text-left transition-colors',
                        active
                          ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]'
                          : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
                      ].join(' ')}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
                          <Bot size={15} />
                          <span className="truncate">{agent.name}</span>
                        </span>
                        <Tag size="small" color={count > 0 ? 'blue' : 'default'}>{count}</Tag>
                      </div>
                      <div className="mt-1 truncate text-xs opacity-70">{agent.id}</div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div className="min-h-0 overflow-y-auto rounded-md border border-[var(--color-border)]">
          <div className="border-b border-[var(--color-border)] px-4 py-3 text-sm font-medium text-[var(--color-text-primary)]">
            时间节点
          </div>
          {loading ? (
            <div className="flex justify-center py-10">
              <Spinner tip="加载历史记忆..." />
            </div>
          ) : groupedTimeline.length === 0 ? (
            <div className="p-6">
              <Empty type="noData" description="暂无历史记忆" />
            </div>
          ) : (
            <div className="divide-y divide-[var(--color-border)]">
              {groupedTimeline.map(([date, nodes]) => (
                <div key={date} className="px-4 py-3">
                  <div className="mb-2 text-xs font-medium text-[var(--color-text-tertiary)]">{date}</div>
                  <div className="space-y-2">
                    {nodes.map((node) => (
                      <button
                        key={node.nodeId}
                        type="button"
                        onClick={() => setSelectedHistoryId(node.nodeId)}
                        className={[
                          'w-full rounded-md p-3 text-left transition-colors',
                          selectedTimelineItem?.nodeId === node.nodeId
                            ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]'
                            : 'bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)]',
                        ].join(' ')}
                      >
                        <div className="line-clamp-1 text-sm font-medium text-[var(--color-text-primary)]">{node.title}</div>
                        <div className="mt-1 line-clamp-2 text-xs text-[var(--color-text-secondary)]">{node.summary}</div>
                        <div className="mt-2 flex flex-wrap gap-1">
                          <Tag size="small" color="blue">{agentNameById.get(node.agentId) || node.agentId}</Tag>
                          {node.tags.slice(0, 3).map((tag) => (
                            <Tag key={tag} size="small" color="mauve">{tag}</Tag>
                          ))}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="min-h-0 overflow-y-auto rounded-md border border-[var(--color-border)]">
          <div className="border-b border-[var(--color-border)] px-4 py-3 text-sm font-medium text-[var(--color-text-primary)]">
            具体记忆
          </div>
          {loading ? (
            <div className="flex justify-center py-12">
              <Spinner tip="查询历史记忆..." />
            </div>
          ) : !selectedTimelineItem ? (
            <div className="p-6">
              <Empty type="noData" description="没有匹配的历史记忆" />
            </div>
          ) : (
            <article className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-[var(--color-text-primary)]">
                    {selectedHistory?.title || selectedTimelineItem.title || selectedTimelineItem.summary.slice(0, 48) || selectedTimelineItem.nodeId}
                  </div>
                  <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                    {selectedHistory?.date || selectedTimelineItem.date} · {agentNameById.get(selectedTimelineItem.agentId) || selectedTimelineItem.agentId}
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap justify-end gap-1">
                  {(selectedHistory?.tags || selectedTimelineItem.tags).slice(0, 5).map((tag) => (
                    <Tag key={tag} size="small" color="blue">{tag}</Tag>
                  ))}
                </div>
              </div>
              <p className="mt-3 text-sm leading-6 text-[var(--color-text-secondary)]">
                {selectedHistory?.summary || selectedTimelineItem.summary}
              </p>
              {selectedHistory?.conversationDigest && (
                <p className="mt-2 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs leading-5 text-[var(--color-text-secondary)]">
                  {selectedHistory.conversationDigest}
                </p>
              )}
              {selectedHistory ? (
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <NodeTextList title="用户输入" items={selectedHistory.userInputs} />
                  <NodeTextList title="Agent 输出" items={selectedHistory.agentFinalOutputs} />
                </div>
              ) : (
                <div className="mt-3 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
                  当前只加载到时间节点摘要，重新查询后可查看完整用户输入和 Agent 输出。
                </div>
              )}
            </article>
          )}
        </div>
      </div>
    </section>
  );
}

function NodeTextList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="min-w-0 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
      <div className="mb-1 text-xs font-medium text-[var(--color-text-tertiary)]">{title}</div>
      {items.length === 0 ? (
        <div className="text-xs text-[var(--color-text-tertiary)]">空</div>
      ) : (
        <div className="space-y-1">
          {items.slice(0, 3).map((item, index) => (
            <div key={`${title}-${index}`} className="line-clamp-3 text-xs leading-5 text-[var(--color-text-secondary)]">
              {item}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
