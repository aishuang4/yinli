/**
 * MemoryPage — 记忆管理页面。
 *
 * 只负责记忆页面的数据装配、刷新入口和视图切换；具体面板放在 features/memory。
 */

import { useEffect, useMemo, useState } from 'react';
import { Brain, RefreshCw } from 'lucide-react';
import { Button } from '@agentscope-ai/design';
import { MarkdownMemoryPanel } from '../features/memory/MarkdownMemoryPanel';
import { NodeMemoryPanel } from '../features/memory/NodeMemoryPanel';
import { SkillEvolutionQueuePanel } from '../features/memory/SkillEvolutionQueuePanel';
import { WikiVaultPanel } from '../features/memory/WikiVaultPanel';
import {
  MARKDOWN_TARGETS,
  MEMORY_TABS,
  type MemoryTab,
} from '../features/memory/memoryViewConfig';
import { useAgents } from '../hooks/useAgents';
import { useMemory } from '../hooks/useMemory';
import type {
  MarkdownMemoryDocument,
  MarkdownMemoryScope,
  MemoryNodeQueryResult,
  MemoryNodeTreeItem,
  MemoryWikiSyncResult,
  SkillEvolutionRecord,
  SkillEvolutionRecordStatus,
} from '../types/memory';

export function MemoryPage() {
  const { agents, loading: agentsLoading, refresh: refreshAgents } = useAgents();
  const {
    readMarkdownMemory,
    saveMarkdownMemory,
    loadNodeTree,
    queryNodes,
    loadSkillEvolutionRecords,
    syncWikiVault,
  } = useMemory();
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
  const [skillStatus, setSkillStatus] = useState<SkillEvolutionRecordStatus | 'all'>('all');
  const [skillAgentId, setSkillAgentId] = useState('');
  const [skillRecords, setSkillRecords] = useState<SkillEvolutionRecord[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [wikiSyncing, setWikiSyncing] = useState(false);
  const [wikiResult, setWikiResult] = useState<MemoryWikiSyncResult | null>(null);
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
    void loadSkillQueue();
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

  async function loadSkillQueue() {
    try {
      setSkillsLoading(true);
      setError('');
      const records = await loadSkillEvolutionRecords({
        status: skillStatus,
        agentId: skillAgentId || undefined,
        limit: 100,
      });
      setSkillRecords(records);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Skill 自进化队列加载失败');
    } finally {
      setSkillsLoading(false);
    }
  }

  async function syncWiki() {
    try {
      setWikiSyncing(true);
      setError('');
      const result = await syncWikiVault(200);
      setWikiResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Memory Wiki 同步失败');
    } finally {
      setWikiSyncing(false);
    }
  }

  function refreshActiveTab() {
    void refreshAgents();
    if (activeTab === 'markdown') void loadMarkdown();
    if (activeTab === 'nodes') void loadNodes();
    if (activeTab === 'skills') void loadSkillQueue();
    if (activeTab === 'wiki') void syncWiki();
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
          <Button icon={<RefreshCw size={14} />} onClick={refreshActiveTab}>
            刷新
          </Button>
        </div>
        <div className="mt-4 grid grid-cols-4 gap-3">
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
        ) : activeTab === 'nodes' ? (
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
        ) : activeTab === 'skills' ? (
          <SkillEvolutionQueuePanel
            agents={agents}
            agentId={skillAgentId}
            setAgentId={setSkillAgentId}
            status={skillStatus}
            setStatus={setSkillStatus}
            records={skillRecords}
            loading={skillsLoading}
            onSearch={loadSkillQueue}
          />
        ) : (
          <WikiVaultPanel
            result={wikiResult}
            syncing={wikiSyncing}
            onSync={syncWiki}
          />
        )}
      </main>
    </div>
  );
}
