/**
 * useEventStream — Chat RunState 事件流 Hook
 *
 * Chat 主链路订阅 RunState 中间层 SSE：
 * - RUN_STATE
 * - RUN_STATE_PATCH
 *
 * AgentScope raw SSE 只在后端投影为 RunState 和 Trace。
 * 新 Chat UI 功能不得直接依赖 raw SSE 作为 live 状态来源。
 *
 * @see docs/chat-sse-event-contract.md
 */

import { useCallback, useRef, useState } from 'react';
import type { ChatRuntimeEvent } from '../types/chatEvents';
import type {
  ChatRunPlanState,
  ChatRunPlanStep,
  ChatRunStatePatch,
  ChatRunStateSnapshot,
  RunStateApprovalSummary,
  RunStateToolSummary,
} from '../types/runState';
import { createId } from '../utils/id';

export type { ChatRunPlanState, ChatRunPlanStep } from '../types/runState';

/** SSE 事件类型 */
export interface SSEEvent {
  type: string;
  data: Record<string, unknown>;
}

/** 流式状态 */
export interface StreamState {
  /** 是否正在接收事件流 */
  isStreaming: boolean;
  /** 当前后台 Run ID */
  currentRunId: string;
  /** 当前 Run 开始时间 */
  runStartedAt: string;
  /** 当前 Run 结束时间 */
  runCompletedAt: string;
  /** 累积的文本内容 */
  textContent: string;
  /** ReAct 过程文本内容 */
  reasoningContent: string;
  /** 最终答案文本内容 */
  finalAnswerContent: string;
  /** 思考链内容（thinking tokens） */
  thinkingContent: string;
  /** 工具调用列表 */
  toolCalls: ToolCallInfo[];
  /** Skill 注入/应用列表 */
  skillCalls: SkillCallInfo[];
  /** Team 协作活动 */
  teamActivities: TeamActivityInfo[];
  /** 模型缓存事件 */
  modelCaches: ModelCacheInfo[];
  /** 应用级运行事件 */
  runtimeEvents: ChatRuntimeEvent[];
  /** RunState 投影出的 Plan Mode 状态 */
  plans: ChatRunPlanState[];
  /** 待人工处理的交互 */
  hitlRequests: HitlRequestInfo[];
  /** 待用户补充信息的反问 */
  clarificationRequests: ClarificationRequestInfo[];
  /** 当前正在调用的模型名称 */
  modelName: string;
  /** Token 用量 */
  tokenUsage: { input: number; output: number } | null;
  /** 错误信息 */
  error: string | null;
}

/** 工具调用信息 */
export interface ToolCallInfo {
  /** 工具调用 ID */
  id: string;
  /** 工具名称 */
  name: string;
  /** 调用参数（JSON 字符串） */
  args: string;
  /** 执行结果 */
  result: string;
  /** 执行状态 */
  status: 'calling' | 'running' | 'completed' | 'failed';
}

function normalizeToolStatus(status: unknown): ToolCallInfo['status'] {
  const value = String(status || 'calling');
  return (
    value === 'running' ||
    value === 'completed' ||
    value === 'failed' ||
    value === 'calling'
      ? value
      : 'calling'
  );
}

function normalizeToolCallRecord(record: Record<string, unknown>): ToolCallInfo {
  return {
    id: String(record.id || record.tool_call_id || createId()),
    name: normalizeToolDisplayName(String(record.name || record.tool_call_name || 'tool')),
    args: String(record.args || ''),
    result: String(record.result || ''),
    status: normalizeToolStatus(record.status),
  };
}

function normalizeToolDisplayName(name: string): string {
  const value = String(name || 'tool');
  const parts = value.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return value;
}

function mergeToolCalls(current: ToolCallInfo[], incoming: ToolCallInfo[]): ToolCallInfo[] {
  const merged = [...current];
  for (const next of incoming) {
    const existingIndex = merged.findIndex((item) => (
      item.id === next.id ||
      (
        item.name === next.name &&
        item.args === next.args &&
        item.result === next.result
      )
    ));
    if (existingIndex === -1) {
      merged.push(next);
      continue;
    }
    const existing = merged[existingIndex];
    merged[existingIndex] = {
      ...existing,
      ...next,
      args: next.args || existing.args,
      result: next.result || existing.result,
      status: next.status || existing.status,
    };
  }
  return merged;
}

/** Skill 应用信息 */
export interface SkillCallInfo {
  id: string;
  name: string;
  description: string;
  trigger: string;
  content: string;
  resourcePath: string;
  resources: string[];
  status: 'running' | 'completed' | 'failed';
}

/** Team 协作活动信息 */
export interface TeamActivityInfo {
  id: string;
  agentId: string;
  agentName: string;
  role: string;
  status: 'pending' | 'running' | 'waiting' | 'completed' | 'failed' | 'skipped';
  task: string;
  assignment: string;
  expectedOutput: string;
  constraints: string;
  detail: string;
  output: string;
  report: string;
  skippedReason: string;
  reportReviewStatus: string;
  reportReviewNotes: string;
  reportReviewGaps: string[];
  thinking: string;
  currentEvent: string;
  toolCalls: ToolCallInfo[];
  executionMode: string;
  dependsOn: string[];
  waitReason: string;
  dependencyOutputCount: number;
  leaderContextCount: number;
  createdAt: string;
}

/** 模型缓存信息 */
export interface ModelCacheInfo {
  id: string;
  modelName: string;
  inputTokens: number;
  outputTokens: number;
  cacheCreationInputTokens: number;
  cacheInputTokens: number;
  cacheHitRate: number;
  estimatedSavedInputTokens: number;
  createdAt: string;
}

/** 人机交互请求 */
export interface HitlRequestInfo {
  interactionId: string;
  type: string;
  sessionId: string;
  agentId: string;
  agentName: string;
  runId: string;
  scope: 'leader' | 'worker';
  teamActivityId: string | null;
  replyId: string;
  toolCalls: Array<{
    id: string;
    name: string;
    input: string;
    rawName?: string;
  }>;
  createdAt: string;
}

export interface ClarificationQuestionInfo {
  id: string;
  type: 'choice' | 'multi_choice' | 'text';
  label: string;
  options: string[];
  placeholder: string;
  required: boolean;
}

export interface ClarificationRequestInfo {
  id: string;
  title: string;
  description: string;
  questions: ClarificationQuestionInfo[];
  createdAt: string;
}

/** Hook 返回值 */
interface UseEventStreamReturn {
  /** 流式状态 */
  state: StreamState;
  /** 发送消息并开始接收事件流 */
  sendMessage: (message: string, options?: SendMessageOptions) => Promise<void>;
  /** 恢复一个暂停的人机交互 */
  resumeInteraction: (
    interactionId: string,
    action: 'approve' | 'reject',
    externalResults?: Record<string, string>
  ) => Promise<void>;
  /** 重新订阅一个仍在后台运行或等待人工处理的 Run */
  subscribeRun: (runId: string) => Promise<void>;
  /** 中止当前流 */
  abort: () => Promise<void>;
  /** 重置状态 */
  reset: () => void;
}

/** 发送消息选项 */
interface SendMessageOptions {
  agentId?: string;
  agentName?: string;
  teamId?: string;
  teamName?: string;
  workerSelectorPrompt?: string;
  teamMembers?: Array<{
    id?: string;
    agentId: string;
    name: string;
    role: string;
    description: string;
    executionMode?: string;
    dependsOn?: string[];
    handoffPrompt?: string;
  }>;
  systemPrompt?: string;
  model?: string;
  sessionId?: string;
  displayMessage?: string;
  skillIds?: string[];
  deepThinking?: boolean;
  sandbox?: boolean;
  approvedPlanId?: string;
  images?: Array<{
    name: string;
    media_type: string;
    data: string;
  }>;
}

/** 初始状态 */
const INITIAL_STATE: StreamState = {
  isStreaming: false,
  currentRunId: '',
  runStartedAt: '',
  runCompletedAt: '',
  textContent: '',
  reasoningContent: '',
  finalAnswerContent: '',
  thinkingContent: '',
  toolCalls: [],
  skillCalls: [],
  teamActivities: [],
  modelCaches: [],
  runtimeEvents: [],
  plans: [],
  hitlRequests: [],
  clarificationRequests: [],
  modelName: '',
  tokenUsage: null,
  error: null,
};

function hasPendingUserInteraction(state: StreamState): boolean {
  return state.hitlRequests.length > 0 || state.clarificationRequests.length > 0;
}

function completeStreamIfNoPendingUserInteraction(state: StreamState): StreamState {
  if (hasPendingUserInteraction(state)) {
    return {
      ...state,
      isStreaming: false,
    };
  }
  return {
    ...state,
    isStreaming: false,
    runCompletedAt: state.runCompletedAt || new Date().toISOString(),
  };
}

function markActiveToolCallsFailed(toolCalls: ToolCallInfo[]): ToolCallInfo[] {
  return toolCalls.map((toolCall) => (
    toolCall.status === 'calling' || toolCall.status === 'running'
      ? { ...toolCall, status: 'failed' }
      : toolCall
  ));
}

function markActiveTeamToolCallsFailed(activities: TeamActivityInfo[]): TeamActivityInfo[] {
  return activities.map((activity) => ({
    ...activity,
    toolCalls: markActiveToolCallsFailed(activity.toolCalls),
  }));
}

function failStreamWithError(state: StreamState, error: string): StreamState {
  return {
    ...state,
    isStreaming: false,
    error,
    toolCalls: markActiveToolCallsFailed(state.toolCalls),
    teamActivities: markActiveTeamToolCallsFailed(state.teamActivities),
    runCompletedAt: state.runCompletedAt || new Date().toISOString(),
  };
}

function streamStateFromRunState(snapshot: ChatRunStateSnapshot): StreamState {
  const runStatus = String(snapshot.status || '');
  const hitlRequests = (snapshot.approvals || [])
    .filter((approval) => approval.status === 'waiting' && !isClarificationApproval(approval))
    .map((approval): HitlRequestInfo => ({
      interactionId: String(approval.interactionId || ''),
      type: String(approval.type || 'tool_approval'),
      sessionId: String(snapshot.sessionId || ''),
      agentId: String(approval.agentId || ''),
      agentName: String(approval.agentName || ''),
      runId: String(snapshot.runId || ''),
      scope: 'leader',
      teamActivityId: null,
      replyId: '',
      toolCalls: (approval.toolCalls || []).map((toolCall) => ({
        id: String(toolCall.id || createId()),
        name: normalizeToolDisplayName(String(toolCall.name || 'tool')),
        input: String(toolCall.input || ''),
        rawName: toolCall.rawName ? String(toolCall.rawName) : undefined,
      })),
      createdAt: String(approval.createdAt || new Date().toISOString()),
    }));
  const clarificationRequests = (snapshot.approvals || [])
    .filter((approval) => approval.status === 'waiting' && isClarificationApproval(approval))
    .map((approval) => clarificationRequestFromRunStateApproval(approval));
  return {
    ...INITIAL_STATE,
    isStreaming: ['running', 'cancelling'].includes(String(snapshot.status || '')),
    currentRunId: String(snapshot.runId || ''),
    runStartedAt: String(snapshot.createdAt || ''),
    runCompletedAt: String(snapshot.completedAt || ''),
    textContent: String(snapshot.finalAnswer || ''),
    reasoningContent: String(snapshot.reasoning || ''),
    finalAnswerContent: String(snapshot.finalAnswer || ''),
    thinkingContent: String(snapshot.thinking || ''),
    toolCalls: (snapshot.tools || []).map((tool) => ({
      id: String(tool.id || createId()),
      name: normalizeToolDisplayName(String(tool.name || 'tool')),
      args: String(tool.inputPreview || ''),
      result: String(tool.error || tool.outputPreview || ''),
      status: normalizeToolStatusForRun(tool.status, runStatus),
    })),
    teamActivities: normalizeTeamActivities(snapshot.teamActivities || [], [], []),
    skillCalls: normalizeRunStateSkillCalls(snapshot.skillCalls || []),
    modelCaches: normalizeRunStateModelCaches(snapshot.modelCaches || []),
    modelName: String(snapshot.modelName || ''),
    tokenUsage: snapshot.tokenUsage || null,
    runtimeEvents: (snapshot.runtimeEvents || []).map((event) => normalizeRuntimeEvent(event)),
    plans: normalizeRunStatePlans(snapshot.plans || []),
    hitlRequests,
    clarificationRequests,
    error: String(snapshot.error || ''),
  };
}

function applyRunStatePatch(state: StreamState, patch: ChatRunStatePatch): StreamState {
  const patchKind = patch.kind || 'snapshot_meta';
  const finalAnswerDelta = patchKind === 'final_answer_delta' ? String(patch.finalAnswerDelta || '') : '';
  const reasoningDelta = patchKind === 'reasoning_delta' ? String(patch.reasoningDelta || '') : '';
  const thinkingDelta = patchKind === 'thinking_delta' ? String(patch.thinkingDelta || '') : '';
  const nextStatus = String(patch.status || '');
  const isTerminal = ['completed', 'failed', 'cancelled'].includes(nextStatus);
  const isWaiting = nextStatus === 'waiting';
  let runtimeEvents = state.runtimeEvents;
  if (patch.runtimeEvent) {
    runtimeEvents = appendRuntimeEventCompact(runtimeEvents, normalizeRuntimeEvent(patch.runtimeEvent));
  }
  let toolCalls = patchKind === 'tool' && patch.tool
    ? mergeToolCalls(state.toolCalls, [toolCallFromRunStateTool(patch.tool)])
    : state.toolCalls;
  if (isTerminal) {
    toolCalls = markActiveToolCallsFailed(toolCalls);
  }
  let hitlRequests = state.hitlRequests;
  let clarificationRequests = state.clarificationRequests;
  if (patchKind === 'approval_resolved' && patch.resolvedApprovalId) {
    hitlRequests = hitlRequests.filter((item) => item.interactionId !== patch.resolvedApprovalId);
    clarificationRequests = clarificationRequests.filter((item) => item.id !== patch.resolvedApprovalId);
  } else if (patchKind === 'approval' && patch.approval && isClarificationApproval(patch.approval)) {
    clarificationRequests = mergeClarificationRequests(
      clarificationRequests,
      clarificationRequestFromRunStateApproval(patch.approval),
    );
  } else if (patchKind === 'approval' && patch.approval) {
    hitlRequests = mergeHitlRequests(
      hitlRequests,
      hitlRequestFromRunStateApproval(patch, patch.approval),
    );
  }
  return {
    ...state,
    currentRunId: String(patch.runId || state.currentRunId),
    runCompletedAt: String(patch.completedAt || state.runCompletedAt || ''),
    textContent: state.textContent + finalAnswerDelta,
    finalAnswerContent: state.finalAnswerContent + finalAnswerDelta,
    reasoningContent: state.reasoningContent + reasoningDelta,
    thinkingContent: state.thinkingContent + thinkingDelta,
    toolCalls,
    teamActivities: patchKind === 'team_activities' && Array.isArray(patch.teamActivities)
      ? normalizeTeamActivities(patch.teamActivities, state.teamActivities, runtimeEvents)
      : state.teamActivities,
    skillCalls: patchKind === 'skill_calls' && Array.isArray(patch.skillCalls)
      ? normalizeRunStateSkillCalls(patch.skillCalls)
      : state.skillCalls,
    modelCaches: patchKind === 'model_usage' && Array.isArray(patch.modelCaches)
      ? normalizeRunStateModelCaches(patch.modelCaches)
      : state.modelCaches,
    modelName: patchKind === 'model_usage' && typeof patch.modelName === 'string'
      ? patch.modelName
      : state.modelName,
    tokenUsage: patchKind === 'model_usage' && patch.tokenUsage !== undefined ? patch.tokenUsage : state.tokenUsage,
    hitlRequests,
    clarificationRequests,
    plans: patchKind === 'plans' && Array.isArray(patch.plans) ? normalizeRunStatePlans(patch.plans) : state.plans,
    runtimeEvents,
    isStreaming: isTerminal || isWaiting ? false : state.isStreaming,
    error: patchKind === 'error' ? String(patch.error || state.error || '') : String(state.error || ''),
  };
}

function normalizeToolStatusForRun(status: unknown, runStatus: string): ToolCallInfo['status'] {
  const normalized = normalizeToolStatus(status);
  if (
    ['completed', 'failed', 'cancelled'].includes(runStatus) &&
    (normalized === 'calling' || normalized === 'running')
  ) {
    return 'failed';
  }
  return normalized;
}

function clarificationRequestFromRunStateApproval(
  approval: RunStateApprovalSummary,
): ClarificationRequestInfo {
  return {
    id: String(approval.interactionId || ''),
    title: String(approval.title || '需要补充信息'),
    description: String(approval.description || ''),
    questions: normalizeRunStateClarificationQuestions(approval.questions || []),
    createdAt: String(approval.createdAt || new Date().toISOString()),
  };
}

function normalizeRunStateClarificationQuestions(questions: Array<Record<string, unknown>>): ClarificationQuestionInfo[] {
  return questions.map((question, index) => {
    const rawType = String(question.type || 'text');
    const type = (
      rawType === 'choice' || rawType === 'multi_choice' || rawType === 'text'
        ? rawType
        : 'text'
    ) as ClarificationQuestionInfo['type'];
    return {
      id: String(question.id || `question-${index + 1}`),
      type,
      label: String(question.label || question.title || `问题 ${index + 1}`),
      options: Array.isArray(question.options) ? question.options.map((option) => String(option)) : [],
      placeholder: String(question.placeholder || ''),
      required: question.required === undefined ? true : Boolean(question.required),
    };
  });
}

function isClarificationApproval(approval: RunStateApprovalSummary): boolean {
  return (
    approval.kind === 'clarification' ||
    approval.type === 'clarification' ||
    (
      Array.isArray(approval.questions) &&
      approval.questions.length > 0 &&
      (!approval.toolCalls || approval.toolCalls.length === 0)
    )
  );
}

function mergeClarificationRequests(
  current: ClarificationRequestInfo[],
  incoming: ClarificationRequestInfo,
): ClarificationRequestInfo[] {
  if (!incoming.id) return current;
  const index = current.findIndex((item) => item.id === incoming.id);
  if (index === -1) {
    return [...current, incoming];
  }
  return current.map((item, itemIndex) => (
    itemIndex === index ? { ...item, ...incoming } : item
  ));
}

function normalizeRunStateSkillCalls(skillCalls: unknown[]): SkillCallInfo[] {
  return skillCalls.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const skill = item as Record<string, unknown>;
    return [{
      id: String(skill.id || skill.name || createId()),
      name: String(skill.name || skill.id || 'Skill'),
      description: String(skill.description || ''),
      trigger: String(skill.trigger || ''),
      content: String(skill.content || ''),
      resourcePath: String(skill.resourcePath || ''),
      resources: Array.isArray(skill.resources) ? skill.resources.map((resource) => String(resource)) : [],
      status: (
        skill.status === 'completed' ||
        skill.status === 'failed' ||
        skill.status === 'running'
          ? skill.status
          : 'running'
      ) as SkillCallInfo['status'],
    }];
  });
}

function normalizeRunStateModelCaches(modelCaches: unknown[]): ModelCacheInfo[] {
  return modelCaches.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const cache = item as Record<string, unknown>;
    return [{
      id: String(cache.id || createId()),
      modelName: String(cache.modelName || ''),
      inputTokens: Number(cache.inputTokens || 0),
      outputTokens: Number(cache.outputTokens || 0),
      cacheCreationInputTokens: Number(cache.cacheCreationInputTokens || 0),
      cacheInputTokens: Number(cache.cacheInputTokens || 0),
      cacheHitRate: Number(cache.cacheHitRate || 0),
      estimatedSavedInputTokens: Number(cache.estimatedSavedInputTokens || 0),
      createdAt: String(cache.createdAt || ''),
    }];
  });
}

function normalizeRunStatePlans(plans: unknown[]): ChatRunPlanState[] {
  return plans.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const plan = item as Record<string, unknown>;
    return [{
      planId: String(plan.planId || ''),
      planFile: String(plan.planFile || ''),
      status: String(plan.status || 'running'),
      markdown: String(plan.markdown || ''),
      steps: Array.isArray(plan.steps) ? plan.steps.map(normalizeRunStatePlanStep) : [],
      updatedAt: String(plan.updatedAt || ''),
    }];
  }).filter((plan) => plan.planId);
}

function normalizeRunStatePlanStep(step: unknown): ChatRunPlanStep {
  if (!step || typeof step !== 'object') return {};
  const record = step as Record<string, unknown>;
  return {
    id: record.id ? String(record.id) : undefined,
    planId: record.planId ? String(record.planId) : undefined,
    stepIndex: typeof record.stepIndex === 'number' ? record.stepIndex : Number(record.stepIndex || 0),
    title: record.title ? String(record.title) : undefined,
    description: record.description ? String(record.description) : undefined,
    status: record.status ? String(record.status) : undefined,
    evidence: record.evidence ? String(record.evidence) : undefined,
    result: record.result ? String(record.result) : undefined,
  };
}

function toolCallFromRunStateTool(tool: RunStateToolSummary): ToolCallInfo {
  return {
    id: String(tool.id || createId()),
    name: normalizeToolDisplayName(String(tool.name || 'tool')),
    args: String(tool.inputPreview || ''),
    result: String(tool.error || tool.outputPreview || ''),
    status: normalizeToolStatus(tool.status),
  };
}

function hitlRequestFromRunStateApproval(
  patch: ChatRunStatePatch,
  approval: RunStateApprovalSummary,
): HitlRequestInfo {
  return {
    interactionId: String(approval.interactionId || ''),
    type: String(approval.type || 'tool_approval'),
    sessionId: String(patch.sessionId || ''),
    agentId: String(approval.agentId || ''),
    agentName: String(approval.agentName || ''),
    runId: String(patch.runId || ''),
    scope: 'leader',
    teamActivityId: null,
    replyId: '',
    toolCalls: (approval.toolCalls || []).map((toolCall) => ({
      id: String(toolCall.id || createId()),
      name: normalizeToolDisplayName(String(toolCall.name || 'tool')),
      input: String(toolCall.input || ''),
      rawName: toolCall.rawName ? String(toolCall.rawName) : undefined,
    })),
    createdAt: String(approval.createdAt || new Date().toISOString()),
  };
}

function mergeHitlRequests(
  current: HitlRequestInfo[],
  incoming: HitlRequestInfo,
): HitlRequestInfo[] {
  if (!incoming.interactionId) return current;
  const index = current.findIndex((item) => item.interactionId === incoming.interactionId);
  if (index === -1) {
    return [...current, incoming];
  }
  return current.map((item, itemIndex) => (
    itemIndex === index ? { ...item, ...incoming } : item
  ));
}

function normalizeRuntimeEvent(data: Record<string, unknown>): ChatRuntimeEvent {
  return {
    id: String(data.id || createId()),
    runId: String(data.runId || ''),
    sessionId: String(data.sessionId || ''),
    type: String(data.type || ''),
    source: String(data.source || 'runtime'),
    sourceEventType: data.sourceEventType ? String(data.sourceEventType) : null,
    scope: String(data.scope || ''),
    agentId: String(data.agentId || ''),
    agentName: String(data.agentName || ''),
    teamActivityId: data.teamActivityId ? String(data.teamActivityId) : null,
    status: String(data.status || ''),
    reason: String(data.reason || ''),
    timestamp: String(data.timestamp || new Date().toISOString()),
    durationMs: typeof data.durationMs === 'number' ? data.durationMs : null,
    payload: (
      typeof data.payload === 'object' && data.payload !== null
        ? data.payload as Record<string, unknown>
        : {}
    ),
  };
}

function appendRuntimeEventCompact(
  current: ChatRuntimeEvent[],
  incoming: ChatRuntimeEvent,
): ChatRuntimeEvent[] {
  const previous = current[current.length - 1];
  if (previous && shouldMergeRuntimeDelta(previous, incoming)) {
    const mergedPayload = {
      ...previous.payload,
      delta: `${String(previous.payload.delta || '')}${String(incoming.payload.delta || '')}`,
    };
    return [
      ...current.slice(0, -1),
      {
        ...previous,
        timestamp: incoming.timestamp,
        payload: mergedPayload,
      },
    ];
  }
  return [...current, incoming].slice(-2000);
}

function shouldMergeRuntimeDelta(previous: ChatRuntimeEvent, incoming: ChatRuntimeEvent): boolean {
  if (previous.type !== incoming.type) return false;
  if (previous.scope !== incoming.scope) return false;
  if (previous.agentId !== incoming.agentId) return false;
  if (previous.teamActivityId !== incoming.teamActivityId) return false;
  const mergeableTypes = new Set<string>([
    'AGENT_OUTPUT_DELTA',
    'AGENT_REASONING_DELTA',
    'AGENT_FINAL_ANSWER_DELTA',
    'AGENT_THINKING',
    'TOOL_CALL_DELTA',
    'TOOL_RESULT_DELTA',
  ]);
  if (!mergeableTypes.has(incoming.type)) {
    return false;
  }
  const previousToolId = String(previous.payload.tool_call_id || '');
  const incomingToolId = String(incoming.payload.tool_call_id || '');
  return previousToolId === incomingToolId;
}

function hasRuntimeActivityEvents(
  runtimeEvents: ChatRuntimeEvent[],
  activityId: string,
  agentId: string,
): boolean {
  return runtimeEvents.some((event) => (
    (activityId && event.teamActivityId === activityId) ||
    (agentId && event.agentId === agentId && ['worker', 'leader', 'single'].includes(event.scope))
  ));
}

function normalizeTeamActivities(
  activities: Array<Record<string, unknown>>,
  current: TeamActivityInfo[],
  runtimeEvents: ChatRuntimeEvent[],
): TeamActivityInfo[] {
  return activities.map((activity) => {
    const existing = current.find((item) => (
      item.id === activity.id || item.agentId === activity.agentId
    ));
    const hasRuntimeState = hasRuntimeActivityEvents(
      runtimeEvents,
      String(activity.id || ''),
      String(activity.agentId || ''),
    );
    const status = String(activity.status || 'pending');
    const toolCalls = Array.isArray(activity.toolCalls)
      ? mergeToolCalls(
        existing?.toolCalls || [],
        activity.toolCalls.map((toolCall) => normalizeToolCallRecord(toolCall as Record<string, unknown>))
      )
      : existing?.toolCalls || [];
    const normalizedStatus = (
      status === 'running' ||
      status === 'waiting' ||
      status === 'completed' ||
      status === 'failed' ||
      status === 'skipped'
        ? status
        : 'pending'
    );
    const incomingTerminal = (
      normalizedStatus === 'completed' ||
      normalizedStatus === 'failed' ||
      normalizedStatus === 'skipped'
    );
    const preserveRuntimeState = Boolean(hasRuntimeState && existing && !incomingTerminal);
    return {
      id: String(activity.id || createId()),
      agentId: String(activity.agentId || ''),
      agentName: String(activity.agentName || 'Agent'),
      role: String(activity.role || 'worker'),
      status: preserveRuntimeState && existing ? existing.status : normalizedStatus,
      task: String(activity.task || ''),
      assignment: String(activity.assignment || existing?.assignment || ''),
      expectedOutput: String(activity.expectedOutput || existing?.expectedOutput || ''),
      constraints: String(activity.constraints || existing?.constraints || ''),
      detail: preserveRuntimeState && existing ? existing.detail : String(activity.detail || existing?.detail || ''),
      output: preserveRuntimeState && existing ? existing.output : String(activity.output || existing?.output || ''),
      report: preserveRuntimeState && existing ? existing.report : String(activity.report || existing?.report || ''),
      skippedReason: String(activity.skippedReason || existing?.skippedReason || ''),
      reportReviewStatus: String(activity.reportReviewStatus || existing?.reportReviewStatus || ''),
      reportReviewNotes: String(activity.reportReviewNotes || existing?.reportReviewNotes || ''),
      reportReviewGaps: Array.isArray(activity.reportReviewGaps)
        ? activity.reportReviewGaps.map((item) => String(item))
        : existing?.reportReviewGaps || [],
      thinking: preserveRuntimeState && existing ? existing.thinking : String(activity.thinking || existing?.thinking || ''),
      currentEvent: incomingTerminal ? String(activity.detail || existing?.currentEvent || '') : existing?.currentEvent || '',
      toolCalls: preserveRuntimeState && existing ? existing.toolCalls : toolCalls,
      executionMode: String(activity.executionMode || existing?.executionMode || 'parallel'),
      dependsOn: Array.isArray(activity.dependsOn)
        ? activity.dependsOn.map((item) => String(item))
        : existing?.dependsOn || [],
      waitReason: String(activity.waitReason || existing?.waitReason || ''),
      dependencyOutputCount: Number(activity.dependencyOutputCount ?? existing?.dependencyOutputCount ?? 0),
      leaderContextCount: Number(activity.leaderContextCount ?? existing?.leaderContextCount ?? 0),
      createdAt: String(activity.createdAt || ''),
    };
  });
}

/**
 * useEventStream Hook
 *
 * 管理 SSE 事件流的完整生命周期：发送请求 → 接收事件 → 更新状态
 */
export function useEventStream(): UseEventStreamReturn {
  const [state, setState] = useState<StreamState>(INITIAL_STATE);
  const abortControllerRef = useRef<AbortController | null>(null);

  const readEventStream = useCallback(async (response: Response) => {
    if (!response.body) {
      throw new Error('Response body is null');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop() || '';

      for (const eventStr of events) {
        if (!eventStr.trim()) continue;
        const parsed = parseSSEEvent(eventStr);
        if (parsed) {
          handleEvent(parsed);
        }
      }
    }
  }, []);

  /** 重置状态到初始值 */
  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState(INITIAL_STATE);
  }, []);

  /** 中止当前流 */
  const abort = useCallback(async () => {
    const runId = state.currentRunId;
    if (runId) {
      try {
        await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/cancel`, {
          method: 'POST',
        });
      } catch (error) {
        console.warn('[SSE] Failed to cancel run:', error);
      }
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState((prev) => ({
      ...prev,
      isStreaming: false,
      error: prev.error || '用户已停止本轮执行。',
      runCompletedAt: prev.runCompletedAt || new Date().toISOString(),
    }));
  }, [state.currentRunId]);

  /** 重新订阅仍在后台运行或等待人工处理的 Run */
  const subscribeRun = useCallback(async (runId: string) => {
    if (!runId) return;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;
    setState((prev) => {
      const isDifferentRun = Boolean(prev.currentRunId && prev.currentRunId !== runId);
      return {
        ...(isDifferentRun ? INITIAL_STATE : prev),
        currentRunId: runId,
        isStreaming: true,
        error: null,
      };
    });

    try {
      const response = await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/state/stream`, {
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      await readEventStream(response);
      setState((prev) => completeStreamIfNoPendingUserInteraction(prev));
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return;
      }
      const errorMessage = err instanceof Error ? err.message : String(err);
      setState((prev) => ({
        ...prev,
        isStreaming: false,
        error: errorMessage,
        runCompletedAt: prev.runCompletedAt || new Date().toISOString(),
      }));
    }
  }, [readEventStream]);

  /** 发送消息并开始接收事件流 */
  const sendMessage = useCallback(
    async (message: string, options?: SendMessageOptions) => {
      // 中止之前的流（如果有）
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      // 创建新的 AbortController
      const controller = new AbortController();
      abortControllerRef.current = controller;

      // 重置状态并开始
      setState({
        ...INITIAL_STATE,
        isStreaming: true,
        runStartedAt: new Date().toISOString(),
      });

      try {
        const response = await fetch('/api/chat/send/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message,
            display_message: options?.displayMessage,
            agent_id: options?.agentId,
            agent_name: options?.agentName,
            team_id: options?.teamId,
            team_name: options?.teamName,
            workerSelectorPrompt: options?.workerSelectorPrompt,
            team_members: options?.teamMembers,
            system_prompt: options?.systemPrompt,
            model: options?.model,
            session_id: options?.sessionId,
            skill_ids: options?.skillIds,
            deep_thinking: options?.deepThinking,
            sandbox: options?.sandbox,
            approvedPlanId: options?.approvedPlanId,
            images: options?.images,
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const payload = await response.json();
        const runId = String(payload?.run?.id || payload?.runId || '');
        if (!runId) {
          throw new Error('Run was not started');
        }
        await subscribeRun(runId);
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          // 用户主动中止
          setState((prev) => completeStreamIfNoPendingUserInteraction(prev));
        } else {
          // 其他错误
          const errorMessage = err instanceof Error ? err.message : String(err);
          setState((prev) => failStreamWithError(prev, errorMessage));
        }
      }
    },
    [subscribeRun]
  );

  /** 恢复暂停的人机交互 */
  const resumeInteraction = useCallback(
    async (
      interactionId: string,
      action: 'approve' | 'reject',
      externalResults: Record<string, string> = {}
    ) => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;
      setState((prev) => ({
        ...prev,
        isStreaming: true,
        error: null,
        hitlRequests: prev.hitlRequests.filter((item) => item.interactionId !== interactionId),
      }));

      try {
        const response = await fetch('/api/chat/resume/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            interactionId,
            action,
            externalResults,
          }),
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        const payload = await response.json();
        const runId = String(payload?.runId || '');
        if (!runId) {
          throw new Error('Run was not resumed');
        }
        await subscribeRun(runId);
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          setState((prev) => completeStreamIfNoPendingUserInteraction(prev));
        } else {
          const errorMessage = err instanceof Error ? err.message : String(err);
          setState((prev) => ({
            ...prev,
            isStreaming: false,
            error: errorMessage,
            runCompletedAt: prev.runCompletedAt || new Date().toISOString(),
          }));
        }
      }
    },
    [subscribeRun]
  );

  /**
   * 解析单个 SSE 事件字符串
   *
   * SSE 格式：
   *   event: TEXT_BLOCK_DELTA
   *   data: {"delta": "你好", ...}
   */
  function parseSSEEvent(raw: string): SSEEvent | null {
    let eventType = '';
    let dataStr = '';

    for (const line of raw.split('\n')) {
      if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        dataStr = line.slice(6);
      }
    }

    if (!eventType || !dataStr) return null;

    try {
      const data = JSON.parse(dataStr);
      return { type: eventType, data };
    } catch {
      console.warn('[SSE] Failed to parse event data:', dataStr);
      return null;
    }
  }

  /**
   * 处理单个 SSE 事件。
   *
   * 主链路只处理 RUN_STATE / RUN_STATE_PATCH。
   * 旧 raw SSE 仅进入后端 RunState 投影和 Trace，不再直接驱动 Chat UI。
   *
   * 遵循 rerender-functional-setstate 规则：
   * 使用函数式 setState 确保基于最新状态更新
   */
  function handleEvent(event: SSEEvent) {
    setState((prev) => {
      switch (event.type) {
        case 'RUN_STATE':
          return streamStateFromRunState(event.data as unknown as ChatRunStateSnapshot);

        case 'RUN_STATE_PATCH':
          return applyRunStatePatch(prev, event.data as unknown as ChatRunStatePatch);

        case 'ERROR':
          return {
            ...prev,
            isStreaming: false,
            runCompletedAt: prev.runCompletedAt || new Date().toISOString(),
            error: (event.data.message as string) || 'Unknown error',
          };

        case 'RUN_CANCELLED':
          return {
            ...prev,
            isStreaming: false,
            runCompletedAt: String(event.data.completed_at || event.data.completedAt || new Date().toISOString()),
            error: '用户已停止本轮执行。',
          };

        default:
          return prev;
      }
    });
  }

  return { state, sendMessage, resumeInteraction, subscribeRun, abort, reset };
}
