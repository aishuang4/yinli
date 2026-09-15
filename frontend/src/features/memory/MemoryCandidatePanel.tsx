/**
 * MemoryCandidatePanel — user / global 层待确认的 Markdown 记忆候选。
 *
 * 模型提炼出的用户偏好和全局规则不再静默写文件，先进这里；采纳后才写入对应 Markdown。
 */

import { useMemo, useState } from 'react';
import { Check, RefreshCw, X } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@/components/design';
import { SelectField } from '../../components/form';
import type { MemoryCandidateAction, MemoryCandidateStatus, MemoryMarkdownCandidate } from '../../types/memory';
import { CANDIDATE_STATUS_OPTIONS, candidateScopeLabel, candidateStatusMeta, formatDateTime } from './memoryViewConfig';

interface MemoryCandidatePanelProps {
  status: MemoryCandidateStatus | 'all';
  setStatus: (value: MemoryCandidateStatus | 'all') => void;
  records: MemoryMarkdownCandidate[];
  pendingCount: number;
  loading: boolean;
  onSearch: () => Promise<void>;
  onAction: (recordId: string, action: MemoryCandidateAction, reason?: string) => Promise<void>;
}

export function MemoryCandidatePanel({
  status,
  setStatus,
  records,
  pendingCount,
  loading,
  onSearch,
  onAction,
}: MemoryCandidatePanelProps) {
  const [busyId, setBusyId] = useState('');
  const grouped = useMemo(() => {
    const map = new Map<string, MemoryMarkdownCandidate[]>();
    for (const record of records) {
      const key = `${record.scope}:${record.ownerId}`;
      map.set(key, [...(map.get(key) || []), record]);
    }
    return [...map.entries()];
  }, [records]);

  async function act(record: MemoryMarkdownCandidate, action: MemoryCandidateAction) {
    setBusyId(record.id);
    try {
      await onAction(record.id, action);
    } finally {
      setBusyId('');
    }
  }

  return (
    <section className="grid h-full min-h-0 grid-rows-[auto_1fr] overflow-hidden p-5">
      <div className="grid grid-cols-[220px_auto_1fr] items-end gap-3">
        <SelectField
          label="状态"
          value={status}
          options={CANDIDATE_STATUS_OPTIONS}
          onChange={(value) => setStatus(value as MemoryCandidateStatus | 'all')}
        />
        <Button type="primary" icon={<RefreshCw size={14} />} onClick={() => void onSearch()} loading={loading}>
          刷新
        </Button>
        <div className="text-xs leading-5 text-[var(--color-text-tertiary)]">
          待确认 {pendingCount} 条。用户 / 全局记忆会影响所有会话，采纳后才写入 Markdown；Agent 自己的工作笔记不经此处，直接写入。
        </div>
      </div>

      <div className="mt-5 min-h-0 overflow-y-auto rounded-md border border-[var(--color-border)]">
        {loading ? (
          <div className="flex justify-center py-10">
            <Spinner tip="加载候选..."><div className="min-h-12 w-12" /></Spinner>
          </div>
        ) : records.length === 0 ? (
          <div className="p-6">
            <Empty type="noData" description={status === 'pending' ? '没有待确认的记忆候选' : '没有匹配的记忆候选'} />
          </div>
        ) : (
          grouped.map(([key, items]) => {
            const first = items[0];
            return (
              <div key={key} className="border-b border-[var(--color-border)] last:border-b-0">
                <div className="flex items-center gap-2 bg-[var(--color-bg-secondary)] px-4 py-2 text-xs font-medium text-[var(--color-text-secondary)]">
                  <span>{candidateScopeLabel(first.scope)}</span>
                  <span className="text-[var(--color-text-tertiary)]">{first.ownerId}</span>
                  <Tag size="small">{items.length} 条</Tag>
                </div>
                <div className="divide-y divide-[var(--color-border)]">
                  {items.map((record) => {
                    const meta = candidateStatusMeta(record.status);
                    const StatusIcon = meta.icon;
                    const body = record.operation === 'append'
                      ? record.note
                      : record.operation === 'update'
                        ? `${record.exactText} → ${record.replacementText}`
                        : record.exactText;
                    return (
                      <div key={record.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 px-4 py-3">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
                            <Tag size="small" color="blue">{record.section}</Tag>
                            <Tag size="small">{operationLabel(record.operation)}</Tag>
                            <Tag size="small" color={meta.color}>
                              <span className="inline-flex items-center gap-1">
                                <StatusIcon size={12} />
                                {meta.label}
                              </span>
                            </Tag>
                            <span>置信度 {Math.round(record.confidence * 100)}%</span>
                            <span>{record.agentName || record.agentId}</span>
                            <span>{formatDateTime(record.createdAt)}</span>
                          </div>
                          <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-[var(--color-text-primary)]">
                            {body}
                          </div>
                          {record.reason ? (
                            <div className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">
                              理由：{record.reason}
                            </div>
                          ) : null}
                          {record.resolution && record.status !== 'pending' ? (
                            <div className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">
                              处理结果：{record.resolution}
                            </div>
                          ) : null}
                        </div>
                        {record.status === 'pending' ? (
                          <div className="flex shrink-0 items-start gap-2">
                            <Button
                              size="small"
                              type="primary"
                              icon={<Check size={13} />}
                              loading={busyId === record.id}
                              onClick={() => void act(record, 'accept')}
                            >
                              采纳
                            </Button>
                            <Button
                              size="small"
                              icon={<X size={13} />}
                              disabled={busyId === record.id}
                              onClick={() => void act(record, 'reject')}
                            >
                              拒绝
                            </Button>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

function operationLabel(operation: MemoryMarkdownCandidate['operation']): string {
  if (operation === 'update') return '修改';
  if (operation === 'delete') return '删除';
  return '新增';
}
