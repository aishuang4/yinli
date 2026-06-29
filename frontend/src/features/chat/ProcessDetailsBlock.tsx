import { useEffect, useRef, useState, type ReactNode } from 'react';
import { BookOpenCheck, Brain, CheckCircle, ChevronDown, ChevronRight, Clock3, GitBranch, Wrench, XCircle } from 'lucide-react';
import { Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import type { ModelCacheInfo, SkillCallInfo, TeamActivityInfo, ToolCallInfo } from '../../hooks/useEventStream';
import { RUNTIME_EVENT_TYPES, type ChatRuntimeEvent } from '../../types/chatEvents';
import { formatProcessDuration } from './chatUiUtils';
import { TeamActivityPanel } from './TeamActivityViews';
import { ToolCallBlock } from './ToolArtifacts';

export function ProcessDetailsBlock({
  defaultExpanded,
  hasFinalAnswer,
  processStartedAt,
  processCompletedAt,
  reactTraceContent,
  thinkingContent,
  teamActivities,
  skillCalls,
  modelCaches,
  runtimeEvents,
  toolCalls,
  selectedTeamActivityId,
  onSelectTeamActivity,
  onApprovePlan,
  onDenyPlan,
}: {
  defaultExpanded: boolean;
  hasFinalAnswer: boolean;
  processStartedAt?: string;
  processCompletedAt?: string;
  reactTraceContent?: string;
  thinkingContent: string;
  teamActivities: TeamActivityInfo[];
  skillCalls: SkillCallInfo[];
  modelCaches: ModelCacheInfo[];
  runtimeEvents: ChatRuntimeEvent[];
  toolCalls: ToolCallInfo[];
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  onApprovePlan?: (event: ChatRuntimeEvent) => Promise<void>;
  onDenyPlan?: (event: ChatRuntimeEvent) => Promise<void>;
}) {
  const hasReactTrace = Boolean((reactTraceContent || '').trim());
  const [expanded, setExpanded] = useState(defaultExpanded && !hasFinalAnswer);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const hadFinalAnswerRef = useRef(hasFinalAnswer);
  const processDuration = formatProcessDuration(processStartedAt, processCompletedAt, nowMs);
  const detailCount = [
    hasReactTrace ? 1 : 0,
    thinkingContent ? 1 : 0,
    teamActivities.length,
    skillCalls.length,
    modelCaches.length,
    toolCalls.length,
    runtimeEvents.length > 0 ? 1 : 0,
  ].reduce((total, count) => total + count, 0);
  const hasProcessDetails = detailCount > 0;

  useEffect(() => {
    if (hasProcessDetails && !hasFinalAnswer) {
      setExpanded(true);
      hadFinalAnswerRef.current = hasFinalAnswer;
      return;
    }
    if (!hadFinalAnswerRef.current && hasFinalAnswer) {
      setExpanded(false);
    }
    hadFinalAnswerRef.current = hasFinalAnswer;
  }, [hasFinalAnswer, hasProcessDetails]);

  useEffect(() => {
    if (processCompletedAt || !processStartedAt) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [processCompletedAt, processStartedAt]);

  if (detailCount === 0) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition hover:bg-[var(--color-bg-hover)]"
      >
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium text-[var(--color-text-primary)]">
            {processCompletedAt ? '已处理' : '处理中'}
          </span>
          {processDuration && (
            <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
              耗时 {processDuration}
            </span>
          )}
          <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
            {detailCount} 项
          </span>
        </div>
        {expanded ? (
          <ChevronDown size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
        )}
      </button>

      {expanded && (
        <ReActTimeline
          reactTraceContent={reactTraceContent || ''}
          thinkingContent={thinkingContent}
          teamActivities={teamActivities}
          skillCalls={skillCalls}
          modelCaches={modelCaches}
          runtimeEvents={runtimeEvents}
          toolCalls={toolCalls}
          selectedTeamActivityId={selectedTeamActivityId}
          onSelectTeamActivity={onSelectTeamActivity}
          onApprovePlan={onApprovePlan}
          onDenyPlan={onDenyPlan}
        />
      )}
    </div>
  );
}

function ReActTimeline({
  reactTraceContent,
  thinkingContent,
  teamActivities,
  skillCalls,
  modelCaches,
  runtimeEvents,
  toolCalls,
  selectedTeamActivityId,
  onSelectTeamActivity,
  onApprovePlan,
  onDenyPlan,
}: {
  reactTraceContent: string;
  thinkingContent: string;
  teamActivities: TeamActivityInfo[];
  skillCalls: SkillCallInfo[];
  modelCaches: ModelCacheInfo[];
  runtimeEvents: ChatRuntimeEvent[];
  toolCalls: ToolCallInfo[];
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  onApprovePlan?: (event: ChatRuntimeEvent) => Promise<void>;
  onDenyPlan?: (event: ChatRuntimeEvent) => Promise<void>;
}) {
  const runtimeItems = buildTimelineItemsFromRuntimeEvents({
    runtimeEvents,
    toolCalls,
    skillCalls,
    onApprovePlan,
    onDenyPlan,
  });
  const fallbackReasoningSegments = splitReasoningSegments([thinkingContent, reactTraceContent].filter(Boolean).join('\n\n'));
  const fallbackActions = buildActionItems({
    modelCaches,
    teamActivities,
    skillCalls,
    toolCalls,
    selectedTeamActivityId,
    onSelectTeamActivity,
  });
  const hasRuntimeTimeline = runtimeItems.length > 0;
  const stepCount = hasRuntimeTimeline
    ? runtimeItems.length
    : Math.max(fallbackReasoningSegments.length, fallbackActions.length);

  return (
    <div className="max-h-[400px] overflow-y-auto border-t border-[var(--color-border)] px-3 py-3 pr-2">
      <div className="space-y-4">
        {hasRuntimeTimeline
          ? runtimeItems.map((item) => item.node)
          : Array.from({ length: stepCount }).map((_, index) => (
            <div key={`react-step-${index}`} className="space-y-2">
              {fallbackReasoningSegments[index] && (
                <ReasoningStep content={fallbackReasoningSegments[index]} />
              )}
              {fallbackActions[index] && fallbackActions[index].node}
            </div>
          ))}
      </div>
    </div>
  );
}

function buildTimelineItemsFromRuntimeEvents({
  runtimeEvents,
  toolCalls,
  skillCalls,
  onApprovePlan,
  onDenyPlan,
}: {
  runtimeEvents: ChatRuntimeEvent[];
  toolCalls: ToolCallInfo[];
  skillCalls: SkillCallInfo[];
  onApprovePlan?: (event: ChatRuntimeEvent) => Promise<void>;
  onDenyPlan?: (event: ChatRuntimeEvent) => Promise<void>;
}): Array<{ id: string; node: ReactNode }> {
  const items: Array<{ id: string; node: ReactNode }> = [];
  const renderedActions = new Set<string>();
  let outputBuffer = '';
  let thinkingBuffer = '';
  const planModeEvents = getPlanModeEvents(runtimeEvents);
  if (planModeEvents.length > 0) {
    items.push({
      id: 'plan-mode',
      node: (
        <ActionShell
          key="plan-mode"
          icon={<BookOpenCheck size={15} />}
          label="Plan Mode"
        >
          <PlanModeRuntimeBlock
            events={planModeEvents}
            onApprovePlan={onApprovePlan}
            onDenyPlan={onDenyPlan}
          />
        </ActionShell>
      ),
    });
  }

  const flushReasoning = (reason: string) => {
    const content = [thinkingBuffer, outputBuffer].filter(Boolean).join('\n\n').trim();
    thinkingBuffer = '';
    outputBuffer = '';
    if (!content) return;
    splitReasoningSegments(content).forEach((segment, index) => {
      items.push({
        id: `reasoning-${reason}-${items.length}-${index}`,
        node: <ReasoningStep key={`reasoning-${reason}-${items.length}-${index}`} content={segment} />,
      });
    });
  };

  for (const event of runtimeEvents) {
    const delta = String(event.payload.delta || '');
    if (event.type === RUNTIME_EVENT_TYPES.AGENT_REASONING_DELTA) {
      outputBuffer += delta;
      continue;
    }
    if (event.type === RUNTIME_EVENT_TYPES.AGENT_FINAL_ANSWER_STARTED) {
      flushReasoning(event.id);
      continue;
    }
    if (event.type === RUNTIME_EVENT_TYPES.AGENT_FINAL_ANSWER_DELTA) {
      continue;
    }
    if (event.type === RUNTIME_EVENT_TYPES.AGENT_THINKING) {
      thinkingBuffer += delta;
      continue;
    }
    if (event.type === RUNTIME_EVENT_TYPES.AGENT_OUTPUT_DELTA) {
      outputBuffer += delta;
      continue;
    }
    if (event.type === RUNTIME_EVENT_TYPES.TOOL_CALL_STARTED) {
      flushReasoning(event.id);
      const toolCallId = String(event.payload.tool_call_id || '');
      if (renderedActions.has(`tool-${toolCallId}`)) continue;
      renderedActions.add(`tool-${toolCallId}`);
      const toolCall = toolCalls.find((item) => item.id === toolCallId);
      if (toolCall) {
        items.push({
          id: `tool-${toolCall.id}`,
          node: <ActionShell key={`tool-${toolCall.id}`} icon={<Wrench size={15} />} label={`Action · ${toolCall.name}`}><ToolCallBlock toolCall={toolCall} /></ActionShell>,
        });
      }
      continue;
    }
    if (event.type === RUNTIME_EVENT_TYPES.SKILL_STARTED || event.type === RUNTIME_EVENT_TYPES.SKILL_COMPLETED) {
      flushReasoning(event.id);
      const skillId = String(event.payload.id || event.payload.skillId || event.payload.skill_id || '');
      const actionId = `skill-${skillId || event.id}`;
      if (renderedActions.has(actionId)) continue;
      const skill = skillCalls.find((item) => item.id === skillId) || skillCalls[0];
      if (skill) {
        renderedActions.add(actionId);
        items.push({
          id: actionId,
          node: <ActionShell key={actionId} icon={<BookOpenCheck size={15} />} label={`Skill · ${skill.name}`}><SkillCallBlock skillCall={skill} /></ActionShell>,
        });
      }
    }
  }

  // 最后一段 AGENT_OUTPUT_DELTA 属于最终答案，由消息正文单独展示；这里只保留 thinking。
  outputBuffer = '';
  flushReasoning('tail');
  return items;
}

function getPlanModeEvents(runtimeEvents: ChatRuntimeEvent[]): ChatRuntimeEvent[] {
  return runtimeEvents.filter((event) => (
    event.type === RUNTIME_EVENT_TYPES.PLAN_MODE_ENTERED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_UPDATED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_WAITING_APPROVAL ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_APPROVED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_DENIED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_TOOL_DENIED
  ));
}

function PlanModeRuntimeBlock({
  events,
  onApprovePlan,
  onDenyPlan,
}: {
  events: ChatRuntimeEvent[];
  onApprovePlan?: (event: ChatRuntimeEvent) => Promise<void>;
  onDenyPlan?: (event: ChatRuntimeEvent) => Promise<void>;
}) {
  const latest = events[events.length - 1];
  const markdown = typeof latest.payload.markdown === 'string' ? latest.payload.markdown : '';
  const planFile = typeof latest.payload.planFile === 'string' ? latest.payload.planFile : '';
  const status = latest.status || latest.type;
  const waitingApproval = latest.type === RUNTIME_EVENT_TYPES.PLAN_WAITING_APPROVAL;

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <div className="space-y-2 px-3 py-2">
        <div className="flex min-w-0 items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {latest.type}
            </div>
            {planFile && (
              <div className="mt-1 truncate font-mono text-xs text-[var(--color-text-tertiary)]">
                {planFile}
              </div>
            )}
          </div>
          <Tag size="small" color={status === 'waiting' ? 'blue' : status === 'failed' ? 'danger' : 'success'}>
            {status}
          </Tag>
        </div>
        {markdown && (
          <div className="max-h-72 overflow-auto rounded-md bg-[var(--color-bg-primary)] p-3 text-xs leading-6 text-[var(--color-text-secondary)]">
            <MarkdownContent content={markdown} />
          </div>
        )}
        {waitingApproval && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void onApprovePlan?.(latest)}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-transparent bg-[var(--color-primary)] px-3 text-xs font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!onApprovePlan}
            >
              <CheckCircle size={14} />
              批准执行
            </button>
            <button
              type="button"
              onClick={() => void onDenyPlan?.(latest)}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 text-xs font-medium text-[var(--color-text-secondary)] transition hover:bg-[var(--color-bg-hover)] disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!onDenyPlan}
            >
              <XCircle size={14} />
              拒绝
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function splitReasoningSegments(content: string): string[] {
  return content
    .split(/\n{2,}/)
    .map((segment) => segment.trim())
    .filter(Boolean)
    .flatMap((segment) => {
      if (segment.length <= 700) return [segment];
      const sentences = segment.split(/(?<=[。！？.!?])\s+/).filter(Boolean);
      const chunks: string[] = [];
      let current = '';
      for (const sentence of sentences) {
        if ((current + sentence).length > 700 && current) {
          chunks.push(current.trim());
          current = '';
        }
        current += `${sentence} `;
      }
      if (current.trim()) chunks.push(current.trim());
      return chunks.length > 0 ? chunks : [segment];
    });
}

function ReasoningStep({ content }: { content: string }) {
  return (
    <div className="flex gap-3">
      <div className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)]">
        <Brain size={15} />
      </div>
      <div className="min-w-0 flex-1 text-sm leading-7 text-[var(--color-text-primary)]">
        <MarkdownContent content={content} />
      </div>
    </div>
  );
}

function buildActionItems({
  modelCaches,
  teamActivities,
  skillCalls,
  toolCalls,
  selectedTeamActivityId,
  onSelectTeamActivity,
}: {
  modelCaches: ModelCacheInfo[];
  teamActivities: TeamActivityInfo[];
  skillCalls: SkillCallInfo[];
  toolCalls: ToolCallInfo[];
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
}): Array<{ id: string; node: ReactNode }> {
  const actions: Array<{ id: string; node: ReactNode }> = [];

  if (modelCaches.length > 0) {
    actions.push({
      id: 'model-cache',
      node: <ActionShell icon={<Clock3 size={15} />} label="服务端 Prompt Cache"><ModelCacheBlock caches={modelCaches} /></ActionShell>,
    });
  }

  if (teamActivities.length > 0) {
    actions.push({
      id: 'team-activity',
      node: (
        <ActionShell icon={<GitBranch size={15} />} label={`Team 协作 · ${teamActivities.length} 项`}>
          <TeamActivityPanel
            activities={teamActivities}
            selectedActivityId={selectedTeamActivityId}
            onSelectActivity={onSelectTeamActivity}
          />
        </ActionShell>
      ),
    });
  }

  skillCalls.forEach((skill) => {
    actions.push({
      id: `skill-${skill.id}`,
      node: <ActionShell icon={<BookOpenCheck size={15} />} label={`Skill · ${skill.name}`}><SkillCallBlock skillCall={skill} /></ActionShell>,
    });
  });

  toolCalls.forEach((toolCall) => {
    actions.push({
      id: `tool-${toolCall.id}`,
      node: <ActionShell icon={<Wrench size={15} />} label={`Action · ${toolCall.name}`}><ToolCallBlock toolCall={toolCall} /></ActionShell>,
    });
  });

  return actions;
}

function ActionShell({
  icon,
  label,
  children,
}: {
  icon: ReactNode;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex gap-3">
      <div className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)]">
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 text-xs font-medium text-[var(--color-text-tertiary)]">
          {label}
        </div>
        {children}
      </div>
    </div>
  );
}

function ModelCacheBlock({ caches }: { caches: ModelCacheInfo[] }) {
  const [expanded, setExpanded] = useState(false);
  const createdTokens = caches.reduce((total, cache) => total + cache.cacheCreationInputTokens, 0);
  const hitTokens = caches.reduce((total, cache) => total + cache.cacheInputTokens, 0);

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition hover:bg-[var(--color-bg-hover)]"
      >
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Clock3 size={14} color="var(--color-accent)" />
          <span>服务端 Prompt Cache</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-xs text-[var(--color-text-tertiary)]">
            创建 {createdTokens} · 命中 {hitTokens}
          </span>
          {expanded ? (
            <ChevronDown size={14} className="text-[var(--color-text-tertiary)]" />
          ) : (
            <ChevronRight size={14} className="text-[var(--color-text-tertiary)]" />
          )}
        </div>
      </button>
      {expanded && (
        <div className="space-y-2 px-3 pb-3">
          {caches.map((cache) => (
            <div
              key={cache.id}
              className="rounded-md bg-[var(--color-bg-primary)] px-3 py-2"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm text-[var(--color-text-primary)]">
                    {cache.modelName || 'model'}
                  </div>
                  <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                    输入 {cache.inputTokens} · 输出 {cache.outputTokens}
                  </div>
                  {(cache.cacheCreationInputTokens > 0 || cache.cacheInputTokens > 0) && (
                    <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                      命中率 {Math.round(cache.cacheHitRate * 100)}% · 约节省输入 {cache.estimatedSavedInputTokens} token
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 flex-wrap justify-end gap-1">
                  <Tag size="small" color={cache.cacheCreationInputTokens > 0 ? 'blue' : 'transparent'}>
                    创建 {cache.cacheCreationInputTokens}
                  </Tag>
                  <Tag size="small" color={cache.cacheInputTokens > 0 ? 'success' : 'transparent'}>
                    命中 {cache.cacheInputTokens}
                  </Tag>
                </div>
              </div>
              {cache.cacheCreationInputTokens === 0 && cache.cacheInputTokens === 0 && (
                <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                  本次模型响应未返回服务端缓存用量。
                </div>
              )}
            </div>
          ))}
          <div className="text-xs text-[var(--color-text-tertiary)]">
            共 {caches.length} 次模型调用返回缓存统计。
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * SkillCallBlock — Skill 注入/应用反馈
 */
function SkillCallBlock({ skillCall }: { skillCall: SkillCallInfo }) {
  const [expanded, setExpanded] = useState(false);
  const statusText = skillCall.status === 'running'
    ? '使用中'
    : skillCall.status === 'failed'
      ? '失败'
      : '已使用';
  const statusColor = skillCall.status === 'running'
    ? 'blue'
    : skillCall.status === 'failed'
      ? 'danger'
      : 'success';

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition hover:bg-[var(--color-bg-hover)]"
      >
        <div className="flex min-w-0 items-center gap-2">
          <BookOpenCheck size={14} color="var(--color-success)" />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {skillCall.name}
            </div>
            <div className="truncate text-xs text-[var(--color-text-tertiary)]">
              {skillCall.trigger || skillCall.description || '已注入 Skill 操作手册'}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Tag size="small" color={statusColor}>
            {statusText}
          </Tag>
          {expanded ? (
            <ChevronDown size={14} className="text-[var(--color-text-tertiary)]" />
          ) : (
            <ChevronRight size={14} className="text-[var(--color-text-tertiary)]" />
          )}
        </div>
      </button>
      {expanded && (
        <div className="space-y-2 px-3 pb-3">
          {skillCall.content && (
            <div className="max-h-72 overflow-auto rounded-md bg-[var(--color-bg-primary)] p-3 text-xs leading-6 text-[var(--color-text-secondary)]">
              <MarkdownContent content={skillCall.content} />
            </div>
          )}
          {skillCall.resources.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {skillCall.resources.slice(0, 8).map((resource) => (
                <Tag key={resource} size="small" color="transparent">
                  {resource}
                </Tag>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
