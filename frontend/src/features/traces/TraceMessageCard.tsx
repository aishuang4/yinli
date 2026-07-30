/**
 * TraceMessageCard — 运行轨迹消息卡片。
 */

import { Bot } from 'lucide-react';
import { Tag } from '@/components/design';
import type { TraceMessage } from './traceTypes';
import { formatLongTextPreview, formatTraceTime, MAX_MESSAGE_PREVIEW_LENGTH } from './traceUtils';
import { TraceToolList } from './TraceToolList';

export function TraceMessageCard({ message }: { message: TraceMessage }) {
  const isUser = message.role === 'user';
  return (
    <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <Bot size={15} color={isUser ? 'var(--color-accent)' : 'var(--color-agent-member)'} />
          <span className="text-sm font-medium text-[var(--color-text-primary)]">
            {isUser ? 'User' : message.agentName || 'Assistant'}
          </span>
          {message.modelName && <Tag size="small" color="transparent">{message.modelName}</Tag>}
        </div>
        <time className="shrink-0 text-xs text-[var(--color-text-tertiary)]">{formatTraceTime(message.createdAt)}</time>
      </div>

      {message.content && (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[var(--color-bg-primary)] p-3 text-sm text-[var(--color-text-secondary)]">
          {formatLongTextPreview(message.content, MAX_MESSAGE_PREVIEW_LENGTH)}
        </pre>
      )}
      {message.error && (
        <div className="mt-3 rounded-md border border-[var(--color-border)] p-3 text-sm text-[var(--color-error)]">
          {message.error}
        </div>
      )}
      {message.toolCalls?.length > 0 && (
        <TraceToolList title="Agent 工具调用" toolCalls={message.toolCalls} />
      )}
    </section>
  );
}
