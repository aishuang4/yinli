import { useCallback, useEffect, useState } from 'react';
import { Activity, Bot, CheckCircle2, Clock3, Copy, RefreshCw, Wrench, XCircle } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../components/MarkdownContent';
import type { TeamActivityInfo, ToolCallInfo } from '../hooks/useEventStream';

interface TraceSummary {
  id: string;
  title: string;
  agentId: string | null;
  agentName: string | null;
  teamId: string | null;
  teamName: string | null;
  messageCount: number;
  toolCallCount: number;
  workerCount: number;
  status: string;
  createdAt: string;
  updatedAt: string;
}

interface TraceMessage {
  id: string;
  role: string;
  content: string;
  agentId: string | null;
  agentName: string | null;
  modelName: string | null;
  error: string | null;
  toolCalls: ToolCallInfo[];
  teamActivities: TeamActivityInfo[];
  createdAt: string;
}

interface TraceEvent {
  id: string;
  sessionId: string;
  eventType: string;
  scope: string;
  agentId: string | null;
  agentName: string | null;
  teamActivityId: string | null;
  status: string;
  title: string;
  content: string;
  payload: Record<string, unknown>;
  createdAt: string;
  sortOrder: number;
}

interface TraceDetail extends TraceSummary {
  messages: TraceMessage[];
  events: TraceEvent[];
}

export function TracePage() {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadTraces = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/traces');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data: TraceSummary[] = await response.json();
      setTraces(data);
      setSelectedId((current) => current || data[0]?.id || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载运行轨迹失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (traceId: string) => {
    if (!traceId) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    try {
      const response = await fetch(`/api/traces/${traceId}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setDetail(await response.json());
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTraces();
  }, [loadTraces]);

  useEffect(() => {
    void loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  return (
    <div className="grid h-full min-h-0 grid-cols-[340px_1fr] gap-4 overflow-hidden p-4">
      <aside className="flex min-h-0 flex-col rounded-md bg-[var(--color-bg-primary)] p-4 shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-medium text-[var(--color-text-primary)]">运行轨迹</h2>
            <div className="mt-1 text-xs text-[var(--color-text-secondary)]">
              {traces.length} 条历史运行
            </div>
          </div>
          <Button size="small" icon={<RefreshCw size={14} />} onClick={() => void loadTraces()} />
        </div>

        {loading ? (
          <div className="flex justify-center py-10"><Spinner /></div>
        ) : error ? (
          <div className="rounded-md border border-[var(--color-border)] p-3 text-sm text-[var(--color-error)]">{error}</div>
        ) : traces.length === 0 ? (
          <Empty type="noData" description="暂无运行轨迹" />
        ) : (
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
            {traces.map((trace) => (
              <button
                key={trace.id}
                type="button"
                onClick={() => setSelectedId(trace.id)}
                className={[
                  'ui-card-transition w-full rounded-md border p-3 text-left',
                  selectedId === trace.id
                    ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg)]'
                    : 'border-[var(--color-border)] bg-[var(--color-bg-primary)] hover:bg-[var(--color-bg-hover)]',
                ].join(' ')}
              >
                <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">{trace.title}</div>
                <div className="mt-1 truncate text-xs text-[var(--color-text-secondary)]">
                  {trace.teamName || trace.agentName || 'Agent'}
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  <Tag size="small" color={trace.status === 'failed' ? 'red' : trace.status === 'waiting' ? 'orange' : 'green'}>
                    {trace.status}
                  </Tag>
                  <Tag size="small" color="transparent">{trace.messageCount} 消息</Tag>
                  <Tag size="small" color="transparent">{trace.toolCallCount} 工具</Tag>
                  {trace.workerCount > 0 && <Tag size="small" color="transparent">{trace.workerCount} Worker</Tag>}
                </div>
              </button>
            ))}
          </div>
        )}
      </aside>

      <main className="min-h-0 overflow-hidden rounded-md bg-[var(--color-bg-primary)] shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
        {detailLoading ? (
          <div className="flex h-full items-center justify-center"><Spinner /></div>
        ) : detail ? (
          <TraceDetailView trace={detail} />
        ) : (
          <div className="flex h-full items-center justify-center text-[var(--color-text-tertiary)]">
            <div className="text-center">
              <Activity size={44} strokeWidth={1.2} className="mx-auto mb-3" />
              <div>选择一条运行轨迹</div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function TraceDetailView({ trace }: { trace: TraceDetail }) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-medium text-[var(--color-text-primary)]">{trace.title}</h2>
            <div className="mt-1 text-sm text-[var(--color-text-secondary)]">
              {trace.teamName ? `${trace.teamName} · ${trace.agentName}` : trace.agentName}
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            <TraceStatus status={trace.status} />
            <Tag size="small" color="transparent">{trace.toolCallCount} 工具调用</Tag>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 pb-5">
        {trace.events && trace.events.length > 0 && (
          <TraceEventTimeline events={trace.events} />
        )}
        {trace.messages.map((message) => (
          <TraceMessageCard key={message.id} message={message} />
        ))}
      </div>
    </div>
  );
}

function TraceEventTimeline({ events }: { events: TraceEvent[] }) {
  return (
    <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Activity size={15} color="var(--color-accent)" />
          <span>事件时间线</span>
        </div>
        <Tag size="small" color="transparent">{events.length} 事件</Tag>
      </div>
      <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
        {events.map((event) => (
          <TraceEventRow key={event.id} event={event} />
        ))}
      </div>
    </section>
  );
}

function TraceEventRow({ event }: { event: TraceEvent }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {event.title || event.eventType}
            </span>
            <Tag size="small" color={event.scope === 'worker' ? 'mauve' : 'blue'}>{event.scope}</Tag>
            {event.status && (
              <Tag size="small" color={event.status === 'failed' ? 'red' : event.status === 'waiting' ? 'orange' : event.status === 'completed' ? 'green' : 'transparent'}>
                {event.status}
              </Tag>
            )}
          </div>
          <div className="mt-1 flex min-w-0 flex-wrap gap-2 text-xs text-[var(--color-text-tertiary)]">
            <span>{event.eventType}</span>
            {event.agentName && <span>{event.agentName}</span>}
          </div>
        </div>
        <time className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
          {formatTraceTime(event.createdAt)}
        </time>
      </div>
      {event.content && (
        <div className="mt-2 line-clamp-3 whitespace-pre-wrap break-words text-xs text-[var(--color-text-secondary)]">
          {event.content}
        </div>
      )}
      {shouldShowTracePayload(event) && (
        <pre className="mt-2 max-h-40 overflow-auto rounded-md bg-[var(--color-bg-secondary)] p-2 text-[11px] text-[var(--color-text-tertiary)]">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      )}
    </div>
  );
}

function shouldShowTracePayload(event: TraceEvent) {
  return (
    event.eventType === 'WORKSPACE_CONFIG' ||
    event.eventType === 'MODEL_CACHE_USAGE' ||
    event.eventType.startsWith('MODEL_FALLBACK_')
  );
}

function TraceMessageCard({ message }: { message: TraceMessage }) {
  const isUser = message.role === 'user';
  return (
    <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <Bot size={15} color={isUser ? 'var(--color-accent)' : 'var(--color-agent-worker)'} />
          <span className="text-sm font-medium text-[var(--color-text-primary)]">
            {isUser ? 'User' : message.agentName || 'Assistant'}
          </span>
          {message.modelName && <Tag size="small" color="transparent">{message.modelName}</Tag>}
        </div>
        <time className="shrink-0 text-xs text-[var(--color-text-tertiary)]">{formatTraceTime(message.createdAt)}</time>
      </div>

      {message.content && (
        <div className="prose-sm max-w-none text-sm text-[var(--color-text-secondary)]">
          <MarkdownContent content={message.content} />
        </div>
      )}
      {message.error && (
        <div className="mt-3 rounded-md border border-[var(--color-border)] p-3 text-sm text-[var(--color-error)]">
          {message.error}
        </div>
      )}
      {message.toolCalls?.length > 0 && (
        <TraceToolList title="Agent 工具调用" toolCalls={message.toolCalls} />
      )}
      {message.teamActivities?.length > 0 && (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {message.teamActivities.map((activity) => (
            <TraceActivityCard key={activity.id} activity={activity} />
          ))}
        </div>
      )}
    </section>
  );
}

function TraceActivityCard({ activity }: { activity: TeamActivityInfo }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">{activity.agentName}</div>
          <div className="text-xs text-[var(--color-text-tertiary)]">{activity.role}</div>
        </div>
        <TraceStatus status={activity.status} />
      </div>
      <div className="line-clamp-2 text-xs text-[var(--color-text-secondary)]">{activity.task || activity.detail}</div>
      {activity.toolCalls?.length > 0 && <TraceToolList title="Worker 工具调用" toolCalls={activity.toolCalls} compact />}
    </div>
  );
}

function TraceToolList({ title, toolCalls, compact = false }: { title: string; toolCalls: ToolCallInfo[]; compact?: boolean }) {
  return (
    <div className="mt-3 space-y-2">
      <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
        <Wrench size={12} />
        <span>{title}</span>
      </div>
      <div className={`${compact ? 'max-h-72' : 'max-h-96'} grid gap-2 overflow-y-auto pr-1`}>
        {toolCalls.map((toolCall) => (
          <div key={toolCall.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-2">
            <div className="flex min-w-0 items-center justify-between gap-2">
              <span className="min-w-0 truncate text-sm font-medium text-[var(--color-text-primary)]" title={normalizeToolDisplayName(toolCall.name)}>
                {normalizeToolDisplayName(toolCall.name)}
              </span>
              <div className="flex shrink-0 items-center gap-1">
                {(toolCall.args || toolCall.result) && (
                  <button
                    type="button"
                    className="inline-flex h-6 w-6 items-center justify-center rounded text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                    title="复制工具内容"
                    onClick={() => void copyTextToClipboard(toolCall.result || toolCall.args)}
                  >
                    <Copy size={12} />
                  </button>
                )}
                <Tag size="small" color={toolCall.status === 'failed' ? 'red' : toolCall.status === 'completed' ? 'green' : 'blue'}>
                  {toolCall.status}
                </Tag>
              </div>
            </div>
            {!compact && (toolCall.args || toolCall.result) && (
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-[var(--color-text-tertiary)]">
                {toolCall.result || toolCall.args}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
}

function normalizeToolDisplayName(name: string): string {
  const value = String(name || 'tool');
  const parts = value.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return value;
}

function TraceStatus({ status }: { status: string }) {
  const Icon = status === 'failed' ? XCircle : status === 'running' || status === 'waiting' ? Clock3 : CheckCircle2;
  return (
    <Tag size="small" color={status === 'failed' ? 'red' : status === 'waiting' ? 'orange' : 'green'}>
      <Icon size={12} />
      {status}
    </Tag>
  );
}

function formatTraceTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
