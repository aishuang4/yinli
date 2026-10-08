/**
 * NodeMemoryPanel — 历史节点记忆检索、时间线和详情面板。
 */

import { useEffect, useMemo, useState } from 'react';
import { Bot, Brain } from 'lucide-react';
import { Empty, Spinner, Tag } from '@/components/design';
import type { Agent } from '../../types/agent';
import type { MemoryNodeQueryResult, MemoryNodeStatus, MemoryNodeTreeItem } from '../../types/memory';
import { NodeMemoryFilters } from './NodeMemoryFilters';
import { NodeTextList } from './MemoryDetailBlocks';

interface NodeMemoryPanelProps {
  agents: Agent[];
  nodeAgentId: string;
  setNodeAgentId: (value: string) => void;
  nodeQuery: string;
  setNodeQuery: (value: string) => void;
  dateFrom: string;
  setDateFrom: (value: string) => void;
  dateTo: string;
  setDateTo: (value: string) => void;
  nodeStatus: MemoryNodeStatus | 'all';
  setNodeStatus: (value: MemoryNodeStatus | 'all') => void;
  loading: boolean;
  nodeTree: MemoryNodeTreeItem[];
  nodeResults: MemoryNodeQueryResult[];
  onSearch: () => Promise<void>;
}

export function NodeMemoryPanel({
  agents,
  nodeAgentId,
  setNodeAgentId,
  nodeQuery,
  setNodeQuery,
  dateFrom,
  setDateFrom,
  dateTo,
  setDateTo,
  nodeStatus,
  setNodeStatus,
  loading,
  nodeTree,
  nodeResults,
  onSearch,
}: NodeMemoryPanelProps) {
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
  const selectedStatus = selectedHistory?.status || selectedTimelineItem?.status || 'active';

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
        status={nodeStatus}
        setStatus={setNodeStatus}
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
              <Spinner tip="加载历史记忆..."><div className="min-h-12 w-12" /></Spinner>
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
                          {node.status !== 'active' && (
                            <Tag size="small" color={nodeStatusColor(node.status)}>{nodeStatusLabel(node.status)}</Tag>
                          )}
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
              <Spinner tip="查询历史记忆..."><div className="min-h-12 w-12" /></Spinner>
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
                  <Tag size="small" color={nodeStatusColor(selectedStatus)}>{nodeStatusLabel(selectedStatus)}</Tag>
                  {(selectedHistory?.tags || selectedTimelineItem.tags).slice(0, 5).map((tag) => (
                    <Tag key={tag} size="small" color="blue">{tag}</Tag>
                  ))}
                </div>
              </div>

              <p className="mt-4 text-xs leading-5 text-[var(--color-text-tertiary)]">
                这些是已停止写入的旧历史节点，只供查看。新的回忆在「时间线」。
              </p>
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

function nodeStatusLabel(status: string): string {
  return {
    active: '活跃',
    disabled: '已禁用',
    archived: '已归档',
    deleted: '已删除',
  }[status] || status;
}

function nodeStatusColor(status: string): 'default' | 'success' | 'blue' | 'mauve' {
  const colors: Record<string, 'default' | 'success' | 'blue' | 'mauve'> = {
    active: 'success',
    disabled: 'mauve',
    archived: 'blue',
    deleted: 'default',
  };
  return colors[status] || 'default';
}
