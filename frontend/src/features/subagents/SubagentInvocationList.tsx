/**
 * SubagentInvocationList — 展示 Subagent 委托调用审计记录。
 */

import { Clock, ExternalLink, Eye, ListChecks } from 'lucide-react';
import { Button, Empty, Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import type { SubagentInvocation } from '../../types/subagent';
import {
  formatDateTime,
  formatDuration,
  deliveryStatusLabel,
  invocationStatusColor,
  invocationStatusLabel,
} from './subagentViewUtils';

interface SubagentInvocationListProps {
  invocations: SubagentInvocation[];
  title?: string;
  emptyDescription?: string;
  onSelect?: (invocation: SubagentInvocation) => void;
}

export function SubagentInvocationList({
  invocations,
  title = '委托历史',
  emptyDescription = '暂无 Subagent 委托记录',
  onSelect,
}: SubagentInvocationListProps) {
  return (
    <section className="grid min-h-0 grid-rows-[auto_1fr] overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3">
        <div className="flex items-center gap-2">
          <ListChecks size={16} color="var(--color-accent)" />
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">{title}</h2>
        </div>
        <Tag size="small" color="blue">{invocations.length} 条</Tag>
      </div>

      <div className="min-h-0 overflow-y-auto p-4">
        {invocations.length === 0 ? (
          <div className="flex min-h-[320px] items-center justify-center">
            <Empty type="noData" description={emptyDescription} />
          </div>
        ) : (
          <div className="grid gap-3">
            {invocations.map((item) => (
              <InvocationCard key={item.id} invocation={item} onSelect={onSelect} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function InvocationCard({ invocation, onSelect }: { invocation: SubagentInvocation; onSelect?: (invocation: SubagentInvocation) => void }) {
  const duration = formatDuration(invocation.startedAt, invocation.completedAt);

  return (
    <article className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{invocation.subagentName}</h3>
            <Tag size="small" color={invocationStatusColor(invocation.status)}>
              {invocationStatusLabel(invocation.status)}
            </Tag>
            <Tag size="small" color={invocation.definitionScope === 'project' ? 'blue' : 'mauve'}>
              {invocation.definitionScope || 'scope'}
            </Tag>
            <Tag size="small" color="transparent">
              {invocation.executionMode || 'attached'}
            </Tag>
            <Tag size="small" color={invocation.deliveryStatus === 'consumed' ? 'green' : 'transparent'}>
              {deliveryStatusLabel(invocation.deliveryStatus)}
            </Tag>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
            <span>{invocation.parentAgentName || invocation.parentAgentId || '主 Agent'}</span>
            <span>·</span>
            <span>{formatDateTime(invocation.startedAt)}</span>
            {duration && (
              <>
                <span>·</span>
                <span className="inline-flex items-center gap-1"><Clock size={12} />{duration}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex max-w-[42%] items-center gap-1 text-xs text-[var(--color-text-tertiary)]">
          <ExternalLink size={12} className="shrink-0" />
          <span className="truncate">{invocation.runId || invocation.sessionId}</span>
        </div>
        {onSelect && <Button icon={<Eye size={14} />} onClick={() => onSelect(invocation)} aria-label="查看任务详情" />}
      </div>

      <div className="mt-3 grid gap-3 text-xs lg:grid-cols-2">
        <MarkdownPanel title="Task" content={invocation.task} />
        <MarkdownPanel
          title={invocation.status === 'failed' ? 'Error' : 'Result'}
          content={invocation.status === 'failed' ? invocation.error : invocation.resultPreview}
          muted={!invocation.resultPreview && !invocation.error}
        />
      </div>

      {invocation.expectedOutput && (
        <div className="mt-3 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
          <div className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">Expected Output</div>
          <MarkdownContent content={invocation.expectedOutput} />
        </div>
      )}
      {invocation.resultRef && (
        <div className="mt-3 truncate font-mono text-xs text-[var(--color-text-tertiary)]" title={invocation.resultRef}>
          {invocation.resultRef}
        </div>
      )}
    </article>
  );
}

function MarkdownPanel({
  title,
  content,
  muted = false,
}: {
  title: string;
  content: string;
  muted?: boolean;
}) {
  return (
    <div className="min-h-[120px] rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
      <div className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">{title}</div>
      {content ? (
        <div className="max-h-56 overflow-y-auto pr-1">
          <MarkdownContent content={content} />
        </div>
      ) : (
        <div className={`text-xs ${muted ? 'text-[var(--color-text-tertiary)]' : 'text-[var(--color-text-secondary)]'}`}>
          暂无内容
        </div>
      )}
    </div>
  );
}
