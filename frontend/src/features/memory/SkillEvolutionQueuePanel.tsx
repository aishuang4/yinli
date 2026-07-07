/**
 * SkillEvolutionQueuePanel — Skill 自进化候选队列。
 */

import { useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@agentscope-ai/design';
import { SelectField } from '../../components/form';
import type { Agent } from '../../types/agent';
import type { SkillEvolutionRecord, SkillEvolutionRecordStatus } from '../../types/memory';
import { SKILL_STATUS_OPTIONS, skillStatusMeta } from './memoryViewConfig';
import { SkillEvolutionRecordDetail } from './SkillEvolutionRecordDetail';

interface SkillEvolutionQueuePanelProps {
  agents: Agent[];
  agentId: string;
  setAgentId: (value: string) => void;
  status: SkillEvolutionRecordStatus | 'all';
  setStatus: (value: SkillEvolutionRecordStatus | 'all') => void;
  records: SkillEvolutionRecord[];
  loading: boolean;
  onSearch: () => Promise<void>;
}

export function SkillEvolutionQueuePanel({
  agents,
  agentId,
  setAgentId,
  status,
  setStatus,
  records,
  loading,
  onSearch,
}: SkillEvolutionQueuePanelProps) {
  const [selectedRecordId, setSelectedRecordId] = useState('');
  const agentOptions = useMemo(() => [
    { label: '全部 Agent', value: '' },
    ...agents.map((agent) => ({ label: agent.name, value: agent.id })),
  ], [agents]);
  const selectedRecord = useMemo(() => (
    records.find((record) => record.id === selectedRecordId) || records[0] || null
  ), [records, selectedRecordId]);

  useEffect(() => {
    if (!records.length) {
      setSelectedRecordId('');
      return;
    }
    if (!records.some((record) => record.id === selectedRecordId)) {
      setSelectedRecordId(records[0].id);
    }
  }, [records, selectedRecordId]);

  return (
    <section className="grid h-full min-h-0 grid-rows-[auto_1fr] overflow-hidden p-5">
      <div className="grid grid-cols-[220px_220px_auto] items-end gap-3">
        <SelectField
          label="状态"
          value={status}
          options={SKILL_STATUS_OPTIONS}
          onChange={(value) => setStatus(value as SkillEvolutionRecordStatus | 'all')}
        />
        <SelectField
          label="Agent"
          value={agentId}
          options={agentOptions}
          onChange={(value) => setAgentId(String(value))}
        />
        <Button type="primary" icon={<RefreshCw size={14} />} onClick={() => void onSearch()} loading={loading}>
          刷新队列
        </Button>
      </div>

      <div className="mt-5 grid min-h-0 grid-cols-[380px_minmax(0,1fr)] gap-5 overflow-hidden">
        <div className="min-h-0 overflow-y-auto rounded-md border border-[var(--color-border)]">
          <div className="border-b border-[var(--color-border)] px-4 py-3 text-sm font-medium text-[var(--color-text-primary)]">
            候选记录
          </div>
          {loading ? (
            <div className="flex justify-center py-10">
              <Spinner tip="加载 Skill 队列..." />
            </div>
          ) : records.length === 0 ? (
            <div className="p-6">
              <Empty type="noData" description="暂无 Skill 自进化记录" />
            </div>
          ) : (
            <div className="divide-y divide-[var(--color-border)]">
              {records.map((record) => {
                const active = selectedRecord?.id === record.id;
                const meta = skillStatusMeta(record.status);
                const StatusIcon = meta.icon;
                return (
                  <button
                    key={record.id}
                    type="button"
                    onClick={() => setSelectedRecordId(record.id)}
                    className={[
                      'w-full px-4 py-3 text-left transition-colors',
                      active ? 'bg-[var(--color-accent-bg)]' : 'hover:bg-[var(--color-bg-hover)]',
                    ].join(' ')}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="line-clamp-1 text-sm font-medium text-[var(--color-text-primary)]">
                          {record.draftSkillName || record.triggerDescription || '未生成草稿'}
                        </div>
                        <div className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--color-text-secondary)]">
                          {record.draftSkillDescription || record.reusableReason || record.routerGate?.reason || '暂无描述'}
                        </div>
                      </div>
                      <Tag size="small" color={meta.color}>
                        <span className="inline-flex items-center gap-1">
                          <StatusIcon size={12} />
                          {meta.label}
                        </span>
                      </Tag>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      <Tag size="small" color="blue">{record.agentName || record.agentId || 'Agent'}</Tag>
                      <Tag size="small" color="mauve">置信度 {Math.round((record.confidence || 0) * 100)}%</Tag>
                      {record.granularity && <Tag size="small" color="default">{record.granularity}</Tag>}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="min-h-0 overflow-y-auto rounded-md border border-[var(--color-border)]">
          <div className="border-b border-[var(--color-border)] px-4 py-3 text-sm font-medium text-[var(--color-text-primary)]">
            队列详情
          </div>
          {loading ? (
            <div className="flex justify-center py-12">
              <Spinner tip="加载详情..." />
            </div>
          ) : !selectedRecord ? (
            <div className="p-6">
              <Empty type="noData" description="选择一条记录查看详情" />
            </div>
          ) : (
            <SkillEvolutionRecordDetail record={selectedRecord} />
          )}
        </div>
      </div>
    </section>
  );
}
