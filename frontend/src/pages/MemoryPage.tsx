/**
 * MemoryPage — 记忆管理页面。
 *
 * 只负责记忆页面的数据装配、刷新入口和视图切换；具体面板放在 features/memory。
 */

import { useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Button } from '@agentscope-ai/design';
import { WorkbenchDetailShell } from '../components/workbench';
import { MarkdownMemoryPanel } from '../features/memory/MarkdownMemoryPanel';
import { MemoryLandingPage } from '../features/memory/MemoryLandingPage';
import { MemorySettingsPanel } from '../features/memory/MemorySettingsPanel';
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
import { useAppStore } from '../stores/appStore';
import type {
  MarkdownMemoryDocument,
  MarkdownMemoryScope,
  MemoryAuditEvent,
  MemoryNodeQueryResult,
  MemoryNodeStatus,
  MemoryRetrievalFeedbackPayload,
  MemoryNodeTreeItem,
  MemorySettingsSnapshot,
  MemoryWikiGraph,
  MemoryWikiSyncResult,
  SkillEvolutionRecordAction,
  SkillEvolutionRecord,
  SkillEvolutionRecordStatus,
} from '../types/memory';

export function MemoryPage() {
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const { agents, loading: agentsLoading, refresh: refreshAgents } = useAgents();
  const {
    readMarkdownMemory,
    saveMarkdownMemory,
    loadNodeTree,
    queryNodes,
    updateNodeStatus,
    loadSkillEvolutionRecords,
    updateSkillEvolutionRecord,
    loadMemoryAuditEvents,
    sendRetrievalFeedback,
    loadMemorySettings,
    saveAdmissionPolicy,
    syncWikiVault,
    loadWikiGraph,
  } = useMemory();
  const [activeTab, setActiveTab] = useState<MemoryTab | null>(null);
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
  const [nodeStatus, setNodeStatus] = useState<MemoryNodeStatus | 'all'>('active');
  const [nodeTree, setNodeTree] = useState<MemoryNodeTreeItem[]>([]);
  const [nodeResults, setNodeResults] = useState<MemoryNodeQueryResult[]>([]);
  const [nodesLoading, setNodesLoading] = useState(false);
  const [skillStatus, setSkillStatus] = useState<SkillEvolutionRecordStatus | 'all'>('all');
  const [skillAgentId, setSkillAgentId] = useState('');
  const [skillRecords, setSkillRecords] = useState<SkillEvolutionRecord[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [wikiSyncing, setWikiSyncing] = useState(false);
  const [wikiResult, setWikiResult] = useState<MemoryWikiSyncResult | null>(null);
  const [wikiGraph, setWikiGraph] = useState<MemoryWikiGraph | null>(null);
  const [wikiGraphLoading, setWikiGraphLoading] = useState(false);
  const [memorySettings, setMemorySettings] = useState<MemorySettingsSnapshot | null>(null);
  const [auditEvents, setAuditEvents] = useState<MemoryAuditEvent[]>([]);
  const [settingsLoading, setSettingsLoading] = useState(false);
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
    if (activeTab !== 'markdown') return;
    if (target !== 'agent' || selectedAgent) {
      void loadMarkdown();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, target, selectedAgentId]);

  useEffect(() => {
    setDoc(null);
    setDraft('');
    setNodeTree([]);
    setNodeResults([]);
    setSkillRecords([]);
    setAuditEvents([]);
    setWikiGraph(null);
    setWikiResult(null);
    void loadNodes();
    void loadSkillQueue();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProjectId]);

  useEffect(() => {
    if (activeTab === 'wiki' && !wikiGraph && !wikiGraphLoading) {
      void loadWikiGraphData();
    }
    if (activeTab === 'settings' && !memorySettings && !settingsLoading) {
      void loadSettingsAndAudit();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

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
        status: nodeStatus,
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

  async function updateHistoryStatus(nodeId: string, status: MemoryNodeStatus, reason = '') {
    try {
      setNodesLoading(true);
      setError('');
      await updateNodeStatus(nodeId, status, reason);
      await loadNodes();
      if (activeTab === 'settings') await loadSettingsAndAudit();
    } catch (err) {
      setError(err instanceof Error ? err.message : '历史记忆状态更新失败');
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

  async function updateSkillRecord(recordId: string, action: SkillEvolutionRecordAction, reason = '') {
    try {
      setSkillsLoading(true);
      setError('');
      await updateSkillEvolutionRecord(recordId, action, reason);
      await loadSkillQueue();
      if (activeTab === 'settings') await loadSettingsAndAudit();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Skill 队列操作失败');
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
      await loadWikiGraphData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Memory Wiki 同步失败');
    } finally {
      setWikiSyncing(false);
    }
  }

  async function loadWikiGraphData() {
    try {
      setWikiGraphLoading(true);
      setError('');
      const graph = await loadWikiGraph(200, 300);
      setWikiGraph(graph);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Memory Wiki 图谱加载失败');
    } finally {
      setWikiGraphLoading(false);
    }
  }

  async function loadSettingsAndAudit() {
    try {
      setSettingsLoading(true);
      setError('');
      const [settings, events] = await Promise.all([
        loadMemorySettings(),
        loadMemoryAuditEvents(80),
      ]);
      setMemorySettings(settings);
      setAuditEvents(events);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Memory 设置或审计加载失败');
    } finally {
      setSettingsLoading(false);
    }
  }

  async function submitRetrievalFeedback(payload: MemoryRetrievalFeedbackPayload) {
    await sendRetrievalFeedback(payload);
    await loadSettingsAndAudit();
  }

  function refreshActiveTab() {
    void refreshAgents();
    if (!activeTab) return;
    if (activeTab === 'markdown') void loadMarkdown();
    if (activeTab === 'nodes') void loadNodes();
    if (activeTab === 'skills') void loadSkillQueue();
    if (activeTab === 'wiki') {
      void syncWiki();
    }
    if (activeTab === 'settings') void loadSettingsAndAudit();
  }

  if (!activeTab) {
    return (
      <MemoryLandingPage
        onSelectTab={setActiveTab}
        onRefresh={() => {
          void refreshAgents();
        }}
      />
    );
  }

  const activeTabInfo = MEMORY_TABS.find((item) => item.key === activeTab) || MEMORY_TABS[0];

  return (
    <WorkbenchDetailShell
      title={activeTabInfo.label}
      subtitle="管理用户、Agent、全局长期记忆和历史记忆。"
      onBack={() => setActiveTab(null)}
      actions={(
        <Button size="small" icon={<RefreshCw size={13} />} onClick={refreshActiveTab}>
          刷新
        </Button>
      )}
    >
      <main className="h-full min-h-0 overflow-hidden bg-[var(--color-bg-primary)]">
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
            nodeStatus={nodeStatus}
            setNodeStatus={setNodeStatus}
            loading={nodesLoading}
            nodeTree={nodeTree}
            nodeResults={nodeResults}
            onSearch={loadNodes}
            onUpdateStatus={updateHistoryStatus}
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
            onAction={updateSkillRecord}
          />
        ) : activeTab === 'wiki' ? (
          <WikiVaultPanel
            result={wikiResult}
            graph={wikiGraph}
            graphLoading={wikiGraphLoading}
            syncing={wikiSyncing}
            onSync={syncWiki}
            onLoadGraph={loadWikiGraphData}
          />
        ) : (
          <MemorySettingsPanel
            settings={memorySettings}
            auditEvents={auditEvents}
            loading={settingsLoading}
            onRefresh={loadSettingsAndAudit}
            onSaveAdmission={async (payload) => {
              const admission = await saveAdmissionPolicy(payload);
              setMemorySettings((current) => (current ? { ...current, admission } : current));
              void loadSettingsAndAudit();
            }}
            onRetrievalFeedback={submitRetrievalFeedback}
          />
        )}
      </main>
    </WorkbenchDetailShell>
  );
}
