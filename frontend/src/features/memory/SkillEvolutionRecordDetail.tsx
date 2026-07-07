/**
 * SkillEvolutionRecordDetail — Skill 自进化候选详情。
 */

import { AlertTriangle, FileText, GitBranch, Sparkles } from 'lucide-react';
import { Tag } from '@agentscope-ai/design';
import type { SkillEvolutionRecord } from '../../types/memory';
import { DetailSection, MetricBlock } from './MemoryDetailBlocks';
import { formatDateTime, skillStatusMeta } from './memoryViewConfig';

export function SkillEvolutionRecordDetail({ record }: { record: SkillEvolutionRecord }) {
  const meta = skillStatusMeta(record.status);
  const StatusIcon = meta.icon;
  const toolCallCount = record.workflowEvidence?.successfulToolCalls?.length || 0;
  const failureCount = record.workflowEvidence?.valuableFailures?.length || 0;
  const changedFileCount = record.workflowEvidence?.changedFiles?.length || 0;

  return (
    <article className="space-y-4 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-base font-semibold text-[var(--color-text-primary)]">
            {record.draftSkillName || '未生成 Skill 草稿'}
          </div>
          <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {formatDateTime(record.updatedAt)} · {record.agentName || record.agentId || '未知 Agent'}
          </div>
        </div>
        <Tag size="small" color={meta.color}>
          <span className="inline-flex items-center gap-1">
            <StatusIcon size={12} />
            {meta.label}
          </span>
        </Tag>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <MetricBlock label="置信度" value={`${Math.round((record.confidence || 0) * 100)}%`} />
        <MetricBlock label="成功工具" value={String(toolCallCount)} />
        <MetricBlock label="失败证据" value={String(failureCount)} />
        <MetricBlock label="改动文件" value={String(changedFileCount)} />
      </div>

      <DetailSection title="Router Gate" icon={<GitBranch size={14} />}>
        <p>{record.routerGate?.reason || '暂无 gate 原因'}</p>
        <div className="mt-2 flex flex-wrap gap-1">
          <Tag size="small" color={record.routerGate?.shouldRun ? 'success' : 'default'}>
            {record.routerGate?.shouldRun ? '允许提炼' : '未进入提炼'}
          </Tag>
          {record.routerGate?.risk && <Tag size="small" color="mauve">风险 {record.routerGate.risk}</Tag>}
          {(record.routerGate?.signals || []).slice(0, 6).map((signal) => (
            <Tag key={signal} size="small" color="blue">{signal}</Tag>
          ))}
        </div>
      </DetailSection>

      <DetailSection title="候选判断" icon={<Sparkles size={14} />}>
        <p>{record.reusableReason || '暂无复用判断'}</p>
        <div className="mt-2 flex flex-wrap gap-1">
          <Tag size="small" color={record.reusable ? 'success' : 'default'}>
            {record.reusable ? '可复用' : '不可复用'}
          </Tag>
          {record.granularity && <Tag size="small" color="blue">粒度 {record.granularity}</Tag>}
          <Tag size="small" color={record.requiresWorkflow ? 'mauve' : 'success'}>
            {record.requiresWorkflow ? '需要 Workflow' : '原子 Skill'}
          </Tag>
          {record.registeredSkillId && <Tag size="small" color="success">已注册 {record.registeredSkillId}</Tag>}
        </div>
      </DetailSection>

      {record.draftSkillDescription && (
        <DetailSection title="草稿描述" icon={<FileText size={14} />}>
          <p>{record.draftSkillDescription}</p>
          {record.draftTags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {record.draftTags.slice(0, 8).map((tag) => (
                <Tag key={tag} size="small" color="mauve">{tag}</Tag>
              ))}
            </div>
          )}
        </DetailSection>
      )}

      {(record.riskNotes || record.suggestedSplit.length > 0) && (
        <DetailSection title="风险与拆分建议" icon={<AlertTriangle size={14} />}>
          {record.riskNotes && <p>{record.riskNotes}</p>}
          {record.suggestedSplit.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {record.suggestedSplit.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
        </DetailSection>
      )}

      {record.variableFlow.length > 0 && (
        <DetailSection title="变量流" icon={<GitBranch size={14} />}>
          <div className="space-y-2">
            {record.variableFlow.slice(0, 4).map((item, index) => (
              <pre key={`variable-${index}`} className="overflow-x-auto rounded-md bg-[var(--color-bg-secondary)] p-2 text-xs leading-5 text-[var(--color-text-secondary)]">
                {JSON.stringify(item, null, 2)}
              </pre>
            ))}
          </div>
        </DetailSection>
      )}
    </article>
  );
}
