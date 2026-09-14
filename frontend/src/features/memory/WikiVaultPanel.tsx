/**
 * WikiVaultPanel — Memory Wiki Vault 同步和本地打开入口。
 */

import { useMemo, useState } from 'react';
import { ExternalLink, FileText, FolderOpen, FolderSync, Network, RefreshCw } from 'lucide-react';
import { Button, Empty, Spinner } from '@/components/design';
import type {
  MemoryWikiGraph,
  MemoryWikiGraphEdge,
  MemoryWikiGraphNode,
  MemoryWikiSyncResult,
} from '../../types/memory';
import { notifyError, notifySuccess } from '../../utils/feedback';
import { DetailSection, MetricBlock } from './MemoryDetailBlocks';

interface WikiVaultPanelProps {
  result: MemoryWikiSyncResult | null;
  graph: MemoryWikiGraph | null;
  graphLoading: boolean;
  syncing: boolean;
  onSync: () => Promise<void>;
  onLoadGraph: () => Promise<void>;
}

export function WikiVaultPanel({
  result,
  graph,
  graphLoading,
  syncing,
  onSync,
  onLoadGraph,
}: WikiVaultPanelProps) {
  const [view, setView] = useState<'graph' | 'files'>('graph');
  const canOpenLocal = Boolean(window.electronAPI);

  async function openVaultPath() {
    if (!result || !window.electronAPI) return;
    const response = await window.electronAPI.openPath(result.path);
    if (response.ok) {
      notifySuccess('已打开 Wiki 文件夹');
    } else {
      notifyError(`打开 Wiki 文件夹失败：${response.error || '未知错误'}`);
    }
  }

  async function openObsidianVault() {
    if (!result || !window.electronAPI) return;
    const url = `obsidian://open?path=${encodeURIComponent(result.path)}`;
    const response = await window.electronAPI.openExternal(url);
    if (response.ok) {
      notifySuccess('已请求 Obsidian 打开 Vault');
    } else {
      notifyError(`打开 Obsidian 失败：${response.error || '未知错误'}`);
    }
  }

  return (
    <section className="grid h-full min-h-0 grid-rows-[auto_1fr] overflow-hidden p-5">
      <div className="flex items-start justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
            <FolderSync size={16} color="var(--color-accent)" />
            Obsidian-compatible Memory Wiki
          </div>
          <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">
            从 Markdown 记忆、历史记忆和已注册 Skills 生成只读 wiki 视图。运行时仍以原始存储为事实源。
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          <Button icon={<RefreshCw size={14} />} onClick={() => void onLoadGraph()} loading={graphLoading}>
            刷新图谱
          </Button>
          <Button type="primary" icon={<FolderSync size={14} />} onClick={() => void onSync()} loading={syncing}>
            同步 Wiki
          </Button>
        </div>
      </div>

      <div className="mt-5 min-h-0 overflow-y-auto rounded-md border border-[var(--color-border)]">
        <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3">
          <div className="text-sm font-medium text-[var(--color-text-primary)]">
            Wiki Vault
          </div>
          <div className="flex rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-1">
            <button
              type="button"
              onClick={() => setView('graph')}
              className={[
                'flex items-center gap-1 rounded px-3 py-1.5 text-xs transition-colors',
                view === 'graph'
                  ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]'
                  : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
              ].join(' ')}
            >
              <Network size={13} />
              图谱
            </button>
            <button
              type="button"
              onClick={() => setView('files')}
              className={[
                'flex items-center gap-1 rounded px-3 py-1.5 text-xs transition-colors',
                view === 'files'
                  ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]'
                  : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
              ].join(' ')}
            >
              <FileText size={13} />
              文件
            </button>
          </div>
        </div>
        {view === 'graph' ? (
          <WikiGraphView graph={graph} loading={graphLoading} onLoadGraph={onLoadGraph} />
        ) : syncing ? (
          <div className="flex justify-center py-12">
            <Spinner tip="同步 Memory Wiki..."><div className="min-h-12 w-12" /></Spinner>
          </div>
        ) : !result ? (
          <div className="p-6">
            <Empty type="noData" description="尚未同步 Wiki Vault" />
          </div>
        ) : (
          <div className="space-y-4 p-4">
            <div className="grid grid-cols-5 gap-3">
              <MetricBlock label="写入文件" value={String(result.filesWritten)} />
              <MetricBlock label="Markdown 记忆" value={String(result.markdownMemories)} />
              <MetricBlock label="历史记忆" value={String(result.historyNodes)} />
              <MetricBlock label="Skills" value={String(result.skills)} />
              <MetricBlock label="实体" value={String(result.entities)} />
            </div>

            <DetailSection title="Vault 路径" icon={<FolderSync size={14} />}>
              <div className="break-all font-mono text-xs">{result.path}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button icon={<FolderOpen size={14} />} onClick={() => void openVaultPath()} disabled={!canOpenLocal}>
                  打开文件夹
                </Button>
                <Button icon={<ExternalLink size={14} />} onClick={() => void openObsidianVault()} disabled={!canOpenLocal}>
                  在 Obsidian 打开
                </Button>
              </div>
              <div className="mt-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
                {canOpenLocal
                  ? 'Obsidian 第一次使用时需要先把该文件夹注册为 vault。'
                  : '当前浏览器环境无法打开本地路径；桌面应用中可直接打开文件夹或 Obsidian。'}
              </div>
            </DetailSection>

            <DetailSection title="生成文件" icon={<FileText size={14} />}>
              {result.files.length === 0 ? (
                <div className="text-xs text-[var(--color-text-tertiary)]">暂无文件。</div>
              ) : (
                <div className="max-h-[360px] overflow-y-auto rounded-md bg-[var(--color-bg-primary)] p-2">
                  {result.files.slice(0, 120).map((file) => (
                    <div key={file} className="border-b border-[var(--color-border)] py-1.5 font-mono text-xs text-[var(--color-text-secondary)] last:border-b-0">
                      {file}
                    </div>
                  ))}
                  {result.files.length > 120 && (
                    <div className="py-2 text-xs text-[var(--color-text-tertiary)]">
                      还有 {result.files.length - 120} 个文件未显示。
                    </div>
                  )}
                </div>
              )}
            </DetailSection>
          </div>
        )}
      </div>
    </section>
  );
}

function WikiGraphView({
  graph,
  loading,
  onLoadGraph,
}: {
  graph: MemoryWikiGraph | null;
  loading: boolean;
  onLoadGraph: () => Promise<void>;
}) {
  const [selectedId, setSelectedId] = useState('');
  const layout = useMemo(() => buildGraphLayout(graph), [graph]);
  const selected = layout.nodes.find((node) => node.id === selectedId) || layout.nodes[0] || null;
  const selectedEdges = selected
    ? graph?.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id).slice(0, 8) || []
    : [];

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Spinner tip="加载 Wiki 图谱..."><div className="min-h-12 w-12" /></Spinner>
      </div>
    );
  }

  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="p-6">
        <Empty type="noData" description="暂无 Wiki 图谱数据" />
        <div className="mt-4 flex justify-center">
          <Button icon={<Network size={14} />} onClick={() => void onLoadGraph()}>
            生成图谱
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="grid min-h-[520px] grid-cols-[minmax(0,1fr)_280px] gap-0">
      <div className="min-w-0 border-r border-[var(--color-border)] bg-[var(--color-bg-primary)]">
        <div className="grid grid-cols-4 gap-3 border-b border-[var(--color-border)] p-4">
          <MetricBlock label="实体节点" value={String(graph.nodes.length)} />
          <MetricBlock label="共现关系" value={String(graph.edges.length)} />
          <MetricBlock label="来源文档" value={String(graph.documents)} />
          <MetricBlock label="历史上限" value={String(graph.historyLimit)} />
        </div>
        <div className="h-[440px] min-h-0 overflow-hidden">
          <svg
            className="h-full w-full"
            role="img"
            aria-label="Memory Wiki 实体图谱"
            viewBox="0 0 760 420"
          >
            <rect width="760" height="420" fill="var(--color-bg-primary)" />
            {layout.edges.map((edge) => {
              const source = layout.nodeById.get(edge.source);
              const target = layout.nodeById.get(edge.target);
              if (!source || !target) return null;
              const highlighted = selected ? edge.source === selected.id || edge.target === selected.id : false;
              return (
                <line
                  key={edge.id}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke={highlighted ? 'var(--color-accent)' : 'var(--color-border)'}
                  strokeOpacity={highlighted ? 0.85 : 0.48}
                  strokeWidth={Math.min(5, 1 + edge.weight)}
                />
              );
            })}
            {layout.nodes.map((node, index) => {
              const selectedNode = selected?.id === node.id;
              const showLabel = selectedNode || index < 18;
              return (
                <g
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedId(node.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') setSelectedId(node.id);
                  }}
                  className="cursor-pointer"
                >
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={node.r}
                    fill={node.color}
                    fillOpacity={selectedNode ? 1 : 0.82}
                    stroke={selectedNode ? 'var(--color-accent)' : 'var(--color-bg-primary)'}
                    strokeWidth={selectedNode ? 3 : 2}
                  />
                  {showLabel && (
                    <text
                      x={node.x}
                      y={node.y + node.r + 13}
                      textAnchor="middle"
                      fill="var(--color-text-secondary)"
                      fontSize="10"
                    >
                      {shortLabel(node.label)}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        </div>
      </div>

      <aside className="min-w-0 bg-[var(--color-bg-secondary)] p-4">
        {selected ? (
          <div className="space-y-4">
            <div>
              <div className="text-xs text-[var(--color-text-tertiary)]">{kindLabel(selected.kind)}</div>
              <div className="mt-1 break-all text-sm font-semibold text-[var(--color-text-primary)]">
                {selected.label}
              </div>
              <div className="mt-2 break-all font-mono text-xs text-[var(--color-text-tertiary)]">
                {selected.path}
              </div>
            </div>
            <DetailSection title="引用文档" icon={<FileText size={14} />}>
              {selected.documents.length === 0 ? (
                <div className="text-xs text-[var(--color-text-tertiary)]">暂无引用。</div>
              ) : selected.documents.slice(0, 8).map((document) => (
                <div key={document} className="break-all border-b border-[var(--color-border)] py-1 font-mono text-xs last:border-b-0">
                  {document}
                </div>
              ))}
            </DetailSection>
            <DetailSection title="相关关系" icon={<Network size={14} />}>
              {selectedEdges.length === 0 ? (
                <div className="text-xs text-[var(--color-text-tertiary)]">暂无关系。</div>
              ) : selectedEdges.map((edge) => (
                <div key={edge.id} className="border-b border-[var(--color-border)] py-1.5 text-xs last:border-b-0">
                  <div className="break-all text-[var(--color-text-secondary)]">
                    {edge.source === selected.id ? edge.target : edge.source}
                  </div>
                  <div className="text-[var(--color-text-tertiary)]">共现 {edge.weight} 次</div>
                </div>
              ))}
            </DetailSection>
          </div>
        ) : (
          <Empty type="noData" description="请选择一个实体" />
        )}
      </aside>
    </div>
  );
}

interface LayoutNode extends MemoryWikiGraphNode {
  x: number;
  y: number;
  r: number;
  color: string;
}

function buildGraphLayout(graph: MemoryWikiGraph | null): {
  nodes: LayoutNode[];
  edges: MemoryWikiGraphEdge[];
  nodeById: Map<string, LayoutNode>;
} {
  if (!graph) return { nodes: [], edges: [], nodeById: new Map() };
  const degree = new Map<string, number>();
  graph.edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) || 0) + edge.weight);
    degree.set(edge.target, (degree.get(edge.target) || 0) + edge.weight);
  });
  const sourceNodes = [...graph.nodes]
    .sort((left, right) => {
      const leftScore = (degree.get(left.id) || 0) + left.documents.length;
      const rightScore = (degree.get(right.id) || 0) + right.documents.length;
      return rightScore - leftScore;
    })
    .slice(0, 80);
  const centerX = 380;
  const centerY = 205;
  const nodes = sourceNodes.map((node, index) => {
    const total = Math.max(sourceNodes.length, 1);
    const angle = (Math.PI * 2 * index) / total - Math.PI / 2;
    const ring = index < 12 ? 120 : index < 36 ? 165 : 205;
    const score = (degree.get(node.id) || 0) + node.documents.length;
    return {
      ...node,
      x: centerX + Math.cos(angle) * ring,
      y: centerY + Math.sin(angle) * ring,
      r: 7 + Math.min(11, Math.sqrt(score + 1) * 2),
      color: kindColor(node.kind),
    };
  });
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const edges = graph.edges.filter((edge) => nodeById.has(edge.source) && nodeById.has(edge.target));
  return { nodes, edges, nodeById };
}

function kindColor(kind: string): string {
  return {
    agent: '#2563eb',
    user: '#0f766e',
    workspace: '#7c3aed',
    skill: '#dc2626',
    topic: '#ca8a04',
    module: '#0891b2',
    rule: '#9333ea',
    storage: '#4f46e5',
    date: '#64748b',
  }[kind] || '#475569';
}

function kindLabel(kind: string): string {
  return {
    agent: 'Agent',
    user: '用户',
    workspace: '工作区',
    skill: 'Skill',
    topic: '主题',
    module: '模块',
    rule: '规则',
    storage: '存储',
    date: '日期',
  }[kind] || kind;
}

function shortLabel(value: string): string {
  return value.length > 14 ? `${value.slice(0, 13)}...` : value;
}
