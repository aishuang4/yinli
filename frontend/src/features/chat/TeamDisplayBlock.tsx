import { AlertCircle, Bot, CheckCircle2, ChevronRight, Clock3, GitBranch, Loader2, Users, X } from 'lucide-react';
import { Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import type { SkillCallInfo, TeamActorInfo, ToolCallInfo } from '../../hooks/useEventStream';
import { DISPLAY_EVENTS, type DisplayItem } from '../../types/displayEvents';
import { normalizeStoredToolCalls } from './formatters';
import { AccordionProcessTimeline } from './ProcessTimeline';
import type { ChatDisplayStatus, ChatProcessItem } from './chatDisplayTypes';

/**
 * TeamDisplayBlock — Team 协作过程展示
 */
export function TeamDisplayBlock({
  actors,
  selectedActorId,
  onSelectActor,
}: {
  actors: TeamActorInfo[];
  selectedActorId: string | null;
  onSelectActor: (id: string) => void;
}) {
  const leader = actors.find((item) => item.role === 'leader');
  const workers = actors.filter((item) => item.role !== 'leader');
  const completedCount = actors.filter((item) => item.status === 'completed').length;

  return (
    <div className="w-full overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Users size={14} color="var(--color-agent-leader)" />
          <span>Team 协作</span>
        </div>
        <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
          {completedCount}/{actors.length} 完成
        </span>
      </div>

      {workers.length > 0 && (
        <TeamExecutionGraph
          leader={leader || null}
          workers={workers}
          selectedActorId={selectedActorId}
          onSelectActor={onSelectActor}
        />
      )}

      <div className="grid items-stretch gap-2 px-3 pb-3 md:grid-cols-2">
        {leader && (
          <TeamActorCard
            actor={leader}
            selected={selectedActorId === leader.id}
            onSelect={() => onSelectActor(leader.id)}
          />
        )}
        {workers.map((actor) => (
          <TeamActorCard
            key={actor.id}
            actor={actor}
            selected={selectedActorId === actor.id}
            onSelect={() => onSelectActor(actor.id)}
          />
        ))}
      </div>
    </div>
  );
}

function TeamExecutionGraph({
  leader,
  workers,
  selectedActorId,
  onSelectActor,
}: {
  leader: TeamActorInfo | null;
  workers: TeamActorInfo[];
  selectedActorId: string | null;
  onSelectActor: (id: string) => void;
}) {
  const nameByAgentId = new Map(workers.map((worker) => [worker.agentId, worker.agentName]));
  const parallelCount = workers.filter((worker) => worker.executionMode !== 'depends_on').length;
  const waitingCount = workers.filter((worker) => worker.status === 'pending' && worker.waitReason).length;

  return (
    <div className="px-3 pb-3">
      <div className="rounded-md bg-[var(--color-bg-primary)] p-3">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs font-medium text-[var(--color-text-secondary)]">
            <GitBranch size={13} color="var(--color-accent)" />
            <span>执行图</span>
          </div>
          <div className="flex flex-wrap gap-1">
            <Tag size="small" color="transparent">并行 {parallelCount}</Tag>
            <Tag size="small" color={waitingCount > 0 ? 'orange' : 'green'}>等待 {waitingCount}</Tag>
            {leader && leader.leaderContextCount > 0 && (
              <Tag size="small" color="blue">Leader 使用 {leader.leaderContextCount} 个输出</Tag>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          {workers.map((worker) => {
            const dependencyNames = worker.dependsOn.map((dependency) => nameByAgentId.get(dependency) || dependency);
            const selected = selectedActorId === worker.id;
            const statusColor = worker.status === 'completed'
              ? 'var(--color-success)'
              : worker.status === 'failed'
                ? 'var(--color-error)'
                : worker.status === 'running'
                  ? 'var(--color-accent)'
                  : 'var(--color-text-tertiary)';
            return (
              <button
                key={worker.id}
                type="button"
                onClick={() => onSelectActor(worker.id)}
                className={`ui-card-transition flex h-[92px] min-w-0 flex-col rounded-md border bg-[var(--color-bg-secondary)] p-2 text-left ${
                  selected ? 'border-[var(--color-accent)] shadow-sm' : 'border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 truncate text-xs font-medium text-[var(--color-text-primary)]">
                    {worker.agentName}
                  </div>
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: statusColor }} />
                </div>
                <div className="mt-1 flex h-6 min-w-0 items-center gap-1 overflow-hidden">
                  <Tag size="small" color={worker.executionMode === 'depends_on' ? 'blue' : 'transparent'}>
                    {worker.executionMode === 'depends_on' ? '依赖执行' : '并行'}
                  </Tag>
                  {worker.dependencyOutputCount > 0 && (
                    <Tag size="small" color="green">收到 {worker.dependencyOutputCount}</Tag>
                  )}
                </div>
                <div className="mt-auto min-w-0 truncate text-[11px] text-[var(--color-text-tertiary)]">
                  {worker.waitReason || (dependencyNames.length > 0 ? `依赖：${dependencyNames.join(' -> ')}` : worker.currentEvent || worker.detail || '')}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function TeamActorCard({
  actor,
  selected,
  onSelect,
}: {
  actor: TeamActorInfo;
  selected: boolean;
  onSelect: () => void;
}) {
  const toolCalls = normalizeStoredToolCalls(actor.toolCalls || []);
  const isRunning = actor.status === 'running';
  const isWaiting = actor.status === 'waiting';
  const isCompleted = actor.status === 'completed';
  const isFailed = actor.status === 'failed';
  const summaryText = actor.report || actor.output || actor.detail || actor.currentEvent || '';
  const reviewLabel = actor.reportReviewStatus && actor.role !== 'leader'
    ? (actor.reportReviewStatus === 'accepted' ? '报告可用' : '需要补充')
    : '';
  const color = isFailed
    ? 'var(--color-error)'
    : isCompleted
      ? 'var(--color-success)'
      : isRunning || isWaiting
        ? 'var(--color-accent)'
        : 'var(--color-text-tertiary)';

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`ui-card-transition flex h-[132px] min-w-0 flex-col rounded-md bg-[var(--color-bg-primary)] p-3 text-left ${
        selected ? 'ring-1 ring-[var(--color-accent)]' : 'hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)]'
      }`}
    >
      <div className="mb-2 flex h-8 w-full shrink-0 items-center gap-2 text-left">
        <div
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-white"
          style={{
            backgroundColor: actor.role === 'leader'
              ? 'var(--color-agent-leader)'
              : 'var(--color-agent-worker)',
          }}
        >
          <Bot size={13} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
            {actor.agentName}
          </div>
          <div className="text-xs text-[var(--color-text-tertiary)]">
            {actor.role === 'leader' ? 'Leader' : 'Worker'}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1 text-xs" style={{ color }}>
          {isRunning ? (
            <Loader2 size={12} className="animate-spin" />
          ) : isWaiting ? (
            <AlertCircle size={12} />
          ) : isCompleted ? (
            <CheckCircle2 size={12} />
          ) : (
            <Clock3 size={12} />
          )}
          <span>{getTeamActivityStatusText(actor.status)}</span>
        </div>
        <ChevronRight size={12} />
      </div>
      <div
        className="h-5 min-w-0 truncate text-sm leading-5 text-[var(--color-text-secondary)]"
        title={actor.assignment || actor.task || ''}
      >
        {actor.assignment || actor.task || ''}
      </div>
      <div
        className="mt-1 h-5 min-w-0 truncate text-xs leading-5 text-[var(--color-text-tertiary)]"
        title={summaryText}
      >
        {summaryText}
      </div>
      <div className="mt-auto flex h-7 min-w-0 items-center gap-1 overflow-hidden pt-1">
        {reviewLabel && (
          <Tag size="small" color={actor.reportReviewStatus === 'accepted' ? 'green' : 'orange'}>
            {reviewLabel}
          </Tag>
        )}
        {toolCalls.length > 0 && (
          <>
          {toolCalls.slice(0, 2).map((toolCall) => (
            <span
              key={toolCall.id}
              className="flex h-[22px] min-w-0 max-w-[160px] items-center truncate rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-1.5 text-xs text-[var(--color-text-tertiary)]"
              title={toolCall.name}
            >
              {toolCall.name}
            </span>
          ))}
          {toolCalls.length > 2 && (
            <span className="flex h-[22px] shrink-0 items-center rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-1.5 text-xs text-[var(--color-text-tertiary)]">
              +{toolCalls.length - 2}
            </span>
          )}
          </>
        )}
      </div>
    </button>
  );
}

export function TeamActorSidebar({
  actor,
  onClose,
}: {
  actor: TeamActorInfo;
  onClose: () => void;
}) {
  const processItems = buildActorProcessItems(actor);
  const isRunning = actor.status === 'running';
  const isWaiting = actor.status === 'waiting';

  return (
    <aside className="flex h-full min-h-0 flex-col bg-[var(--color-bg-secondary)] shadow-inner">
      <div className="flex items-start gap-3 px-4 py-4">
        <div
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white"
          style={{
            backgroundColor: actor.role === 'leader'
              ? 'var(--color-agent-leader)'
              : 'var(--color-agent-worker)',
          }}
        >
          <Bot size={16} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-base font-medium text-[var(--color-text-primary)]">
            {actor.agentName}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
            <span>{actor.role === 'leader' ? 'Leader' : 'Worker'}</span>
            <span>{getTeamActivityStatusText(actor.status)}</span>
          </div>
        </div>
        <button
          type="button"
          className="icon-button h-8 w-8 rounded-md text-[var(--color-text-secondary)] transition-all duration-300 ease-out hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
          onClick={onClose}
          aria-label="关闭详情"
          title="关闭详情"
        >
          <X size={14} />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 pb-4">
        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">当前动作</div>
          <div className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
            {isRunning && <Loader2 size={13} className="animate-spin text-[var(--color-accent)]" />}
            {isWaiting && <AlertCircle size={13} className="text-[var(--color-warning)]" />}
            <span>{actor.currentEvent || getTeamActivityStatusText(actor.status)}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
            <Tag size="small" color={actor.executionMode === 'depends_on' ? 'blue' : 'transparent'}>
              {actor.role === 'leader'
                ? 'Leader 汇总'
                : actor.executionMode === 'depends_on'
                  ? '依赖执行'
                  : '并行执行'}
            </Tag>
            {actor.dependencyOutputCount > 0 && (
              <Tag size="small" color="green">上游输出 {actor.dependencyOutputCount}</Tag>
            )}
            {actor.leaderContextCount > 0 && (
              <Tag size="small" color="blue">Worker 输出 {actor.leaderContextCount}</Tag>
            )}
            {actor.reportReviewStatus && (
              <Tag size="small" color={actor.reportReviewStatus === 'accepted' ? 'green' : 'orange'}>
                {actor.reportReviewStatus === 'accepted' ? '报告可用' : '需要补充'}
              </Tag>
            )}
          </div>
          {actor.waitReason && (
            <div className="mt-2 text-xs text-[var(--color-warning)]">
              {actor.waitReason}
            </div>
          )}
        </section>

        {actor.dependsOn.length > 0 && (
          <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
            <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">依赖 Worker</div>
            <div className="flex flex-wrap gap-1">
              {actor.dependsOn.map((dependency) => (
                <Tag key={dependency} size="small" color="transparent">
                  {dependency}
                </Tag>
              ))}
            </div>
          </section>
        )}

        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">分派任务</div>
          <div className="whitespace-pre-wrap break-words text-sm text-[var(--color-text-secondary)]">
            {actor.assignment || actor.task}
          </div>
          {actor.expectedOutput && (
            <div className="mt-3">
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">期望输出</div>
              <div className="whitespace-pre-wrap break-words text-xs text-[var(--color-text-secondary)]">
                {actor.expectedOutput}
              </div>
            </div>
          )}
          {actor.constraints && (
            <div className="mt-3">
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">约束</div>
              <div className="whitespace-pre-wrap break-words text-xs text-[var(--color-text-tertiary)]">
                {actor.constraints}
              </div>
            </div>
          )}
          {actor.detail && (
            <div className="mt-2 whitespace-pre-wrap break-words text-xs text-[var(--color-text-tertiary)]">
              {actor.detail}
            </div>
          )}
          {actor.skippedReason && (
            <div className="mt-2 whitespace-pre-wrap break-words text-xs text-[var(--color-warning)]">
              跳过原因：{actor.skippedReason}
            </div>
          )}
          {actor.reportReviewStatus && actor.role !== 'leader' && (
            <div className="mt-3 rounded-md bg-[var(--color-bg-secondary)] p-2">
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">报告质量</div>
              <div className="text-xs text-[var(--color-text-secondary)]">
                {actor.reportReviewStatus === 'accepted' ? '报告可用于 Leader 汇总' : '报告信息不足，Leader 会按缺口谨慎汇总'}
              </div>
              {actor.reportReviewNotes && (
                <div className="mt-1 whitespace-pre-wrap break-words text-xs text-[var(--color-text-tertiary)]">
                  {actor.reportReviewNotes}
                </div>
              )}
              {actor.reportReviewGaps.length > 0 && (
                <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-[var(--color-warning)]">
                  {actor.reportReviewGaps.map((gap, index) => (
                    <li key={`${gap}-${index}`}>{gap}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>

        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">
            {actor.role === 'leader' ? '输出' : 'Worker report'}
          </div>
          {actor.report || actor.output ? (
            <div className="max-h-80 overflow-auto text-xs text-[var(--color-text-secondary)]">
              <MarkdownContent content={actor.report || actor.output} />
            </div>
          ) : (
            <div className="text-sm text-[var(--color-text-tertiary)]">暂无输出</div>
          )}
        </section>

        {processItems.length > 0 && (
          <AccordionProcessTimeline
            accordionKey={`team-actor-process-${actor.id}-${actor.status}`}
            items={processItems}
            autoScroll={actor.status === 'running' || actor.status === 'waiting'}
            title="运行过程"
            status={actorProcessStatus(actor.status)}
            rightChildren={(
              <span className="text-xs text-[var(--color-text-tertiary)]">
                {processItems.length} 项
              </span>
            )}
            defaultOpen
            alwaysOpen
          />
        )}
      </div>
    </aside>
  );
}

function buildActorProcessItems(actor: TeamActorInfo): ChatProcessItem[] {
  const items = Array.isArray(actor.items) ? actor.items : [];
  const processItems = items
    .map(actorDisplayItemToProcessItem)
    .filter((item): item is ChatProcessItem => Boolean(item));
  if (processItems.length > 0) return normalizeActorProcessItems(processItems);
  return normalizeStoredToolCalls(actor.toolCalls || []).map((toolCall): ChatProcessItem => ({
    id: `tool-${toolCall.id}`,
    type: 'tool',
    toolCall,
    status: toolCall.status,
    updatedAt: actor.createdAt,
  }));
}

function normalizeActorProcessItems(items: ChatProcessItem[]): ChatProcessItem[] {
  return items.reduce<ChatProcessItem[]>((normalized, item) => {
    if (item.type !== 'tool') {
      normalized.push(item);
      return normalized;
    }
    const toolKey = canonicalToolProcessKey(item);
    const existingIndex = normalized.findIndex((current) => (
      current.type === 'tool' && canonicalToolProcessKey(current) === toolKey
    ));
    if (existingIndex >= 0) {
      normalized[existingIndex] = mergeToolProcessItem(normalized[existingIndex], item);
      return normalized;
    }
    normalized.push(item);
    return normalized;
  }, []);
}

function canonicalToolProcessKey(item: ChatProcessItem): string {
  if (item.type !== 'tool') return item.id;
  return canonicalToolCallId(item.toolCall.id || item.id);
}

function canonicalToolCallId(id: string): string {
  const value = String(id || '');
  if (value.includes(':tool:')) {
    return value.split(':tool:').pop() || value;
  }
  return value.replace(/^tool[:-]/, '') || value;
}

function mergeToolProcessItem(current: ChatProcessItem, incoming: ChatProcessItem): ChatProcessItem {
  if (current.type !== 'tool' || incoming.type !== 'tool') return incoming;
  const currentTool = current.toolCall;
  const incomingTool = incoming.toolCall;
  const canonicalId = canonicalToolCallId(incomingTool.id || currentTool.id || incoming.id || current.id);
  const name = preferToolName(currentTool.name, incomingTool.name);
  const args = preferLongerText(currentTool.args, incomingTool.args);
  const result = preferLongerText(currentTool.result, incomingTool.result);
  const status = preferToolStatus(currentTool.status, incomingTool.status);
  return {
    ...current,
    id: canonicalId || incoming.id || current.id,
    status,
    updatedAt: incoming.updatedAt || current.updatedAt,
    toolCall: {
      ...currentTool,
      ...incomingTool,
      id: canonicalId || incomingTool.id || currentTool.id,
      name,
      args,
      result,
      status,
    },
  };
}

function preferToolName(current: string, incoming: string): string {
  const currentName = normalizeToolDisplayName(String(current || 'tool'));
  const incomingName = normalizeToolDisplayName(String(incoming || 'tool'));
  if (incomingName !== 'tool') return incomingName;
  if (currentName !== 'tool') return currentName;
  return incomingName || currentName || 'tool';
}

function preferLongerText(current: string, incoming: string): string {
  const currentText = String(current || '');
  const incomingText = String(incoming || '');
  return incomingText.length >= currentText.length ? incomingText : currentText;
}

function preferToolStatus(
  current: ToolCallInfo['status'],
  incoming: ToolCallInfo['status'],
): ToolCallInfo['status'] {
  if (incoming === 'failed' || incoming === 'completed') return incoming;
  if (current === 'failed' || current === 'completed') return current;
  return incoming || current;
}

function actorDisplayItemToProcessItem(item: DisplayItem): ChatProcessItem | null {
  const status = String(item.status || 'running') as ChatDisplayStatus;
  if (isToolDisplayItem(item)) {
    return {
      id: item.id,
      type: 'tool',
      toolCall: toolCallFromDisplayItem(item),
      status,
      updatedAt: item.updatedAt,
    };
  }
  if (isSkillDisplayItem(item)) {
    return {
      id: item.id,
      type: 'skill',
      skillCall: skillCallFromDisplayItem(item),
      status,
      updatedAt: item.updatedAt,
    };
  }
  if (isReasoningDisplayItem(item)) {
    const content = pickDisplayItemString(item, ['text', 'content', 'delta']);
    return content ? { id: item.id, type: 'reasoning', content, status, updatedAt: item.updatedAt } : null;
  }
  if (isThinkingDisplayItem(item)) {
    const content = pickDisplayItemString(item, ['text', 'content', 'delta']);
    return content ? { id: item.id, type: 'thinking', content, status, updatedAt: item.updatedAt } : null;
  }
  return null;
}

function isToolDisplayItem(item: DisplayItem): boolean {
  const targetId = String(item.targetId || item.id || '');
  return item.event.startsWith('tool.') || targetId.includes(':tool:') || targetId.startsWith('tool');
}

function isSkillDisplayItem(item: DisplayItem): boolean {
  const targetId = String(item.targetId || item.id || '');
  return item.event.startsWith('skill.') || targetId.includes(':skill:') || targetId.startsWith('skill');
}

function isReasoningDisplayItem(item: DisplayItem): boolean {
  const targetId = String(item.targetId || '');
  return item.event === DISPLAY_EVENTS.REASONING_DELTA || targetId === 'reasoning' || targetId.endsWith(':reasoning') || targetId.startsWith('reasoning-');
}

function isThinkingDisplayItem(item: DisplayItem): boolean {
  const targetId = String(item.targetId || '');
  return item.event === DISPLAY_EVENTS.THINKING_DELTA || targetId === 'thinking' || targetId.endsWith(':thinking') || targetId.startsWith('thinking-');
}

function toolCallFromDisplayItem(item: DisplayItem): ToolCallInfo {
  return {
    id: item.targetId || item.id,
    name: normalizeToolDisplayName(pickDisplayItemString(item, ['name', 'toolName', 'rawName']) || 'tool'),
    args: pickDisplayItemString(item, ['inputPreview', 'input', 'args']),
    result: pickDisplayItemString(item, ['outputPreview', 'output', 'result', 'error']),
    status: normalizeToolStatus(item.status || item.data.status),
  };
}

function skillCallFromDisplayItem(item: DisplayItem): SkillCallInfo {
  const status = String(item.status || item.data.status || 'running');
  return {
    id: item.targetId || item.id,
    name: pickDisplayItemString(item, ['name']) || 'Skill',
    description: pickDisplayItemString(item, ['description']),
    trigger: pickDisplayItemString(item, ['trigger']),
    content: pickDisplayItemString(item, ['content']),
    resourcePath: pickDisplayItemString(item, ['resourcePath']),
    resources: Array.isArray(item.data.resources) ? item.data.resources.map((resource) => String(resource)) : [],
    status: (
      status === 'completed' || status === 'failed' || status === 'running'
        ? status
        : 'running'
    ) as SkillCallInfo['status'],
  };
}

function pickDisplayItemString(item: DisplayItem, keys: string[]): string {
  for (const key of keys) {
    const value = item.data[key];
    if (value === undefined || value === null) continue;
    const text = String(value);
    if (text) return text;
  }
  return '';
}

function normalizeToolStatus(status: unknown): ToolCallInfo['status'] {
  const value = String(status || 'calling');
  if (value === 'running' || value === 'completed' || value === 'failed' || value === 'calling') {
    return value;
  }
  return 'calling';
}

function normalizeToolDisplayName(name: string): string {
  const value = String(name || 'tool');
  const parts = value.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return value;
}

function actorProcessStatus(status: TeamActorInfo['status']): ChatDisplayStatus {
  if (status === 'skipped') return 'completed';
  return status;
}

function getTeamActivityStatusText(status: TeamActorInfo['status']) {
  if (status === 'running') return '执行中';
  if (status === 'waiting') return '等待确认';
  if (status === 'completed') return '完成';
  if (status === 'failed') return '失败';
  if (status === 'skipped') return '跳过';
  return '等待';
}
