/** SubagentTaskDetail — 单个持久任务与 transcript 详情。 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Ban, Clock, FileText, ListTree, RefreshCw } from 'lucide-react';
import { Button, Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import type { SubagentInvocation, SubagentTranscriptItem } from '../../types/subagent';
import { formatDateTime, formatDuration, invocationStatusColor, invocationStatusLabel } from './subagentViewUtils';

interface SubagentTaskDetailProps {
  invocation: SubagentInvocation;
  projectId: string;
  onRefresh: () => Promise<void>;
}

export function SubagentTaskDetail({ invocation, projectId, onRefresh }: SubagentTaskDetailProps) {
  const [items, setItems] = useState<SubagentTranscriptItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);

  const loadTranscript = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      const response = await fetch(`/api/subagents/invocations/${encodeURIComponent(invocation.id)}/transcript?projectId=${encodeURIComponent(projectId)}`, { cache: 'no-store' });
      if (!response.ok) throw new Error('加载 transcript 失败');
      const data = await response.json() as { items: SubagentTranscriptItem[] };
      setItems(data.items);
      setTranscriptError(null);
    } catch (err) {
      setTranscriptError(err instanceof Error ? err.message : '加载 transcript 失败');
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [invocation.id, projectId]);

  useEffect(() => {
    void loadTranscript();
  }, [loadTranscript]);

  useEffect(() => {
    const active = invocation.status === 'queued'
      || invocation.status === 'running'
      || invocation.status === 'waiting';
    if (!active) return undefined;
    const timer = window.setInterval(() => {
      void loadTranscript(false);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [invocation.status, loadTranscript]);

  const cancel = async () => {
    setCancelling(true);
    try {
      const response = await fetch(`/api/subagents/invocations/${encodeURIComponent(invocation.id)}/cancel?projectId=${encodeURIComponent(projectId)}`, { method: 'POST' });
      if (!response.ok) throw new Error('取消任务失败');
      await onRefresh();
    } finally {
      setCancelling(false);
    }
  };

  const canCancel = invocation.status === 'queued' || invocation.status === 'running';
  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border)] pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <Tag size="small" color={invocationStatusColor(invocation.status)}>{invocationStatusLabel(invocation.status)}</Tag>
          <Tag size="small" color="transparent">{invocation.id}</Tag>
          <span className="inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)]"><Clock size={13} />{formatDateTime(invocation.startedAt)} {formatDuration(invocation.startedAt, invocation.completedAt)}</span>
        </div>
        <div className="flex items-center gap-2">
          <Button icon={<RefreshCw size={14} />} onClick={() => void Promise.all([loadTranscript(), onRefresh()])} loading={loading} aria-label="刷新任务" />
          {canCancel && <Button icon={<Ban size={14} />} onClick={() => void cancel()} loading={cancelling} aria-label="取消任务" />}
        </div>
      </div>
      <section className="grid gap-3 lg:grid-cols-2">
        <TaskPanel icon={<FileText size={15} />} title="委托任务" content={invocation.task} />
        <TaskPanel icon={<FileText size={15} />} title={invocation.status === 'failed' ? '错误' : '结果'} content={invocation.status === 'failed' ? invocation.error : invocation.resultPreview} />
      </section>
      <section className="grid gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]"><ListTree size={16} />Transcript</div>
        <div className="border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
          {transcriptError ? (
            <div className="p-5 text-sm text-[var(--color-error)]">{transcriptError}</div>
          ) : items.length === 0 && !loading ? (
            <div className="p-5 text-sm text-[var(--color-text-tertiary)]">暂无可展示的过程记录</div>
          ) : items.map((item) => <TranscriptRow key={item.id} item={item} />)}
        </div>
      </section>
    </div>
  );
}

function TaskPanel({ icon, title, content }: { icon: ReactNode; title: string; content: string }) {
  return <section className="border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4"><div className="mb-3 flex items-center gap-2 text-sm font-semibold">{icon}{title}</div>{content ? <MarkdownContent content={content} /> : <div className="text-sm text-[var(--color-text-tertiary)]">暂无内容</div>}</section>;
}

function TranscriptRow({ item }: { item: SubagentTranscriptItem }) {
  const payload = JSON.stringify(item.payload, null, 2);
  return <div className="border-b border-[var(--color-border)] px-4 py-3 last:border-b-0"><div className="flex items-center justify-between gap-3"><span className="font-mono text-xs text-[var(--color-text-secondary)]">{String(item.seq).padStart(3, '0')} {item.kind}</span><span className="text-xs text-[var(--color-text-tertiary)]">{formatDateTime(item.createdAt)}</span></div><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs text-[var(--color-text-secondary)]">{payload}</pre></div>;
}
