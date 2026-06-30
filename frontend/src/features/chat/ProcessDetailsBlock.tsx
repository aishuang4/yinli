import { useEffect, useRef, useState, type ReactNode } from 'react';
import { BookOpenCheck, Brain, CheckCircle, ChevronDown, ChevronRight, Clock3, GitBranch, ListChecks, Wrench, X, XCircle } from 'lucide-react';
import { Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import type { ChatRunPlanState, ModelCacheInfo, SkillCallInfo, TeamActivityInfo, ToolCallInfo } from '../../hooks/useEventStream';
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
}) {
  const hasReactTrace = Boolean((reactTraceContent || '').trim());
  const [expanded, setExpanded] = useState(defaultExpanded && !hasFinalAnswer);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const hadFinalAnswerRef = useRef(hasFinalAnswer);
  const processDuration = formatProcessDuration(processStartedAt, processCompletedAt, nowMs);
  const detailRuntimeEvents = runtimeEvents.filter((event) => !isPlanModeRuntimeEvent(event));
  const detailCount = [
    hasReactTrace ? 1 : 0,
    thinkingContent ? 1 : 0,
    teamActivities.length,
    skillCalls.length,
    modelCaches.length,
    toolCalls.length,
    detailRuntimeEvents.length > 0 ? 1 : 0,
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
          runtimeEvents={detailRuntimeEvents}
          toolCalls={toolCalls}
          selectedTeamActivityId={selectedTeamActivityId}
          onSelectTeamActivity={onSelectTeamActivity}
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
}) {
  const runtimeItems = buildTimelineItemsFromRuntimeEvents({
    runtimeEvents,
    toolCalls,
    skillCalls,
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
}: {
  runtimeEvents: ChatRuntimeEvent[];
  toolCalls: ToolCallInfo[];
  skillCalls: SkillCallInfo[];
}): Array<{ id: string; node: ReactNode }> {
  const items: Array<{ id: string; node: ReactNode }> = [];
  const renderedActions = new Set<string>();
  let outputBuffer = '';
  let thinkingBuffer = '';

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

export function isPlanModeRuntimeEvent(event: ChatRuntimeEvent): boolean {
  return (
    event.type === RUNTIME_EVENT_TYPES.PLAN_MODE_ENTERED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_UPDATED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_WAITING_APPROVAL ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_APPROVED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_DENIED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_TOOL_DENIED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_TASKS_UPDATED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_TASK_OUTPUT
  );
}

export function getPlanModeEvents(runtimeEvents: ChatRuntimeEvent[]): ChatRuntimeEvent[] {
  return runtimeEvents.filter(isPlanModeRuntimeEvent);
}

type PlanTaskStep = {
  id?: unknown;
  stepIndex?: unknown;
  title?: unknown;
  description?: unknown;
  status?: unknown;
  evidence?: unknown;
  result?: unknown;
};

function planTaskStatusColor(status: string): string {
  if (status === 'failed') return 'danger';
  if (status === 'completed') return 'success';
  if (status === 'running') return 'blue';
  if (status === 'skipped') return 'transparent';
  return 'orange';
}

export function PlanModeDock({
  events,
  plans = [],
  resolvedPlanIds,
  onApprovePlan,
  onDenyPlan,
}: {
  events: ChatRuntimeEvent[];
  plans?: ChatRunPlanState[];
  resolvedPlanIds?: Set<string>;
  onApprovePlan?: (event: ChatRuntimeEvent) => Promise<void>;
  onDenyPlan?: (event: ChatRuntimeEvent) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  if (plans.length > 0) {
    const latestPlan = plans[plans.length - 1];
    const latestPlanId = latestPlan.planId;
    const locallyResolved = Boolean(latestPlanId && resolvedPlanIds?.has(latestPlanId));
    const waitingApproval = latestPlan.status === 'waiting' && !locallyResolved;
    const actionEvent = runtimeEventFromPlanState(latestPlan);
    return (
      <PlanModeDockContent
        open={open}
        setOpen={setOpen}
        planId={latestPlanId}
        planFile={latestPlan.planFile}
        markdown={latestPlan.markdown}
        steps={latestPlan.steps}
        status={locallyResolved ? 'completed' : latestPlan.status}
        waitingApproval={waitingApproval}
        actionEvent={actionEvent}
        onApprovePlan={onApprovePlan}
        onDenyPlan={onDenyPlan}
      />
    );
  }
  const latest = events[events.length - 1];
  if (!latest) return null;
  const latestPlanId = String(latest.payload.planId || '');
  const relatedEvents = latestPlanId
    ? events.filter((event) => String(event.payload.planId || '') === latestPlanId)
    : events;
  const latestRelated = relatedEvents[relatedEvents.length - 1] || latest;
  const locallyResolved = Boolean(latestPlanId && resolvedPlanIds?.has(latestPlanId));
  const waitingEvent = [...relatedEvents].reverse().find((event) => (
    event.type === RUNTIME_EVENT_TYPES.PLAN_WAITING_APPROVAL
  ));
  const terminalEvent = [...relatedEvents].reverse().find((event) => (
    event.type === RUNTIME_EVENT_TYPES.PLAN_APPROVED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_DENIED
  ));
  const markdownEvent = [...relatedEvents].reverse().find((event) => (
    typeof event.payload.markdown === 'string' && event.payload.markdown
  )) || latestRelated;
  const markdown = typeof markdownEvent.payload.markdown === 'string' ? markdownEvent.payload.markdown : '';
  const stepsEvent = [...relatedEvents].reverse().find((event) => Array.isArray(event.payload.steps));
  const steps = (Array.isArray(stepsEvent?.payload.steps) ? stepsEvent.payload.steps : []) as PlanTaskStep[];
  const planFile = typeof latestRelated.payload.planFile === 'string' ? latestRelated.payload.planFile : '';
  const waitingApproval = Boolean(waitingEvent && !terminalEvent && !locallyResolved);
  const actionEvent = waitingEvent || latestRelated;
  const status = terminalEvent?.status || (locallyResolved ? 'completed' : waitingApproval ? 'waiting' : latestRelated.status || latestRelated.type);
  return (
    <PlanModeDockContent
      open={open}
      setOpen={setOpen}
      planId={latestPlanId}
      planFile={planFile}
      markdown={markdown}
      steps={steps}
      status={status}
      waitingApproval={waitingApproval}
      actionEvent={actionEvent}
      onApprovePlan={onApprovePlan}
      onDenyPlan={onDenyPlan}
    />
  );
}

function PlanModeDockContent({
  open,
  setOpen,
  planFile,
  markdown,
  steps,
  status,
  waitingApproval,
  actionEvent,
  onApprovePlan,
  onDenyPlan,
}: {
  open: boolean;
  setOpen: (value: boolean | ((current: boolean) => boolean)) => void;
  planId: string;
  planFile: string;
  markdown: string;
  steps: PlanTaskStep[];
  status: string;
  waitingApproval: boolean;
  actionEvent: ChatRuntimeEvent;
  onApprovePlan?: (event: ChatRuntimeEvent) => Promise<void>;
  onDenyPlan?: (event: ChatRuntimeEvent) => Promise<void>;
}) {
  const completedCount = steps.filter((step) => {
    const stepStatus = String(step.status || '');
    return stepStatus === 'completed' || stepStatus === 'skipped';
  }).length;
  const runningIndex = steps.findIndex((step) => String(step.status || '') === 'running');
  const currentStep = steps.length > 0
    ? runningIndex >= 0
      ? runningIndex + 1
      : Math.min(completedCount + 1, steps.length)
    : 0;
  const progressLabel = steps.length > 0
    ? completedCount >= steps.length
      ? `${completedCount}/${steps.length} 完成`
      : `第 ${currentStep}/${steps.length} 步`
    : waitingApproval
      ? '待审批'
      : String(status || 'plan');
  const statusLabel = waitingApproval
    ? '待审批'
    : status === 'completed'
      ? '执行中'
      : status === 'failed'
        ? '已拒绝'
        : String(status || 'Plan');

  return (
    <div className="pointer-events-none absolute bottom-16 left-1/2 z-40 flex max-w-[calc(100%-40px)] -translate-x-1/2 flex-col items-center gap-2">
      {open && (
        <div className="pointer-events-auto w-[min(560px,calc(100vw-72px))] overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-primary)] shadow-xl">
          <div className="flex min-w-0 items-center justify-between gap-3 border-b border-[var(--color-border)] px-3 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <ListChecks size={15} className="shrink-0 text-[var(--color-text-secondary)]" />
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">Plan Mode</div>
                {planFile && (
                  <div className="truncate font-mono text-[11px] text-[var(--color-text-tertiary)]">
                    {planFile}
                  </div>
                )}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Tag size="small" color={waitingApproval ? 'blue' : status === 'failed' ? 'danger' : 'success'}>
                {statusLabel}
              </Tag>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                aria-label="关闭计划面板"
              >
                <X size={14} />
              </button>
            </div>
          </div>
          <div className="max-h-[420px] space-y-3 overflow-auto p-3">
            {steps.length > 0 && (
              <div className="space-y-2">
                {steps.map((step, index) => {
                  const statusText = String(step.status || 'pending');
                  const descriptionText = String(step.description || '');
                  const resultText = String(step.result || step.evidence || '');
                  return (
                    <div
                      key={String(step.id || index)}
                      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2"
                    >
                      <div className="flex min-w-0 items-center justify-between gap-2">
                        <div className="min-w-0 truncate text-xs font-medium text-[var(--color-text-primary)]">
                          {Number(step.stepIndex ?? index) + 1}. {String(step.title || 'Untitled task')}
                        </div>
                        <Tag size="small" color={planTaskStatusColor(statusText)}>
                          {statusText}
                        </Tag>
                      </div>
                      {descriptionText && (
                        <div className="mt-1 line-clamp-2 text-xs text-[var(--color-text-tertiary)]">
                          {descriptionText}
                        </div>
                      )}
                      {resultText && (
                        <div className="mt-1 line-clamp-2 text-xs text-[var(--color-text-secondary)]">
                          {resultText}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
            {markdown && (
              <div className="max-h-64 overflow-auto rounded-lg bg-[var(--color-bg-secondary)] p-3 text-xs leading-6 text-[var(--color-text-secondary)]">
                <MarkdownContent content={markdown} />
              </div>
            )}
          </div>
        </div>
      )}
      <div className="pointer-events-auto flex max-w-full items-center overflow-hidden rounded-full border border-[var(--color-border)] bg-[var(--color-bg-primary)]/90 shadow-lg backdrop-blur">
        {waitingApproval && (
          <button
            type="button"
            onClick={() => void onDenyPlan?.(actionEvent)}
            className="inline-flex h-9 shrink-0 items-center gap-1.5 px-3 text-xs font-medium text-[var(--color-error)] transition hover:bg-[var(--color-error-bg)] disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!onDenyPlan}
          >
            <XCircle size={14} />
            拒绝
          </button>
        )}
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className={`inline-flex h-9 min-w-0 items-center gap-2 px-3 text-sm font-medium text-[var(--color-text-primary)] transition hover:bg-[var(--color-bg-hover)] ${
            waitingApproval ? 'border-x border-[var(--color-border)]' : ''
          }`}
        >
          <ListChecks size={15} className="shrink-0" />
          <span className="truncate">计划</span>
          <span className="shrink-0 rounded-full bg-[var(--color-bg-tertiary)] px-2 py-0.5 text-xs text-[var(--color-text-secondary)]">
            {progressLabel}
          </span>
        </button>
        {waitingApproval && (
            <button
              type="button"
              onClick={() => void onApprovePlan?.(actionEvent)}
              className="inline-flex h-9 shrink-0 items-center gap-1.5 px-3 text-xs font-medium text-[var(--color-success)] transition hover:bg-[var(--color-success-bg)] disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!onApprovePlan}
            >
              <CheckCircle size={14} />
              批准执行
            </button>
        )}
      </div>
    </div>
  );
}

function runtimeEventFromPlanState(plan: ChatRunPlanState): ChatRuntimeEvent {
  return {
    id: `plan-state-${plan.planId}-${plan.updatedAt || 'latest'}`,
    runId: '',
    sessionId: '',
    type: plan.status === 'waiting'
      ? RUNTIME_EVENT_TYPES.PLAN_WAITING_APPROVAL
      : plan.status === 'failed'
        ? RUNTIME_EVENT_TYPES.PLAN_DENIED
        : RUNTIME_EVENT_TYPES.PLAN_UPDATED,
    source: 'run_state',
    sourceEventType: null,
    scope: 'plan',
    agentId: '',
    agentName: '',
    teamActivityId: null,
    status: plan.status,
    reason: '',
    timestamp: plan.updatedAt || new Date().toISOString(),
    durationMs: null,
    payload: {
      planId: plan.planId,
      planFile: plan.planFile,
      markdown: plan.markdown,
      steps: plan.steps,
    },
  };
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
