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
import { DISPLAY_BLOCKS, DISPLAY_EVENTS, type DisplayBlock, type DisplaySnapshot } from '../types/displayEvents';
import type {
  ChatRunPlanState,
  ChatRunPlanStep,
  ChatRunStatePatch,
  ChatRunStateSnapshot,
  RunStateApprovalSummary,
} from '../types/runState';
import { applyDisplayEvent, createEmptyDisplaySnapshot, normalizeDisplaySnapshot } from '../utils/displayEventReducer';
import { notifySuccess } from '../utils/feedback';
import { createId } from '../utils/id';
import { subagentToolDataFromDisplayItem } from '../features/chat/subagentToolData';
import type { SubagentTimelineItem } from '../types/subagent';
import { applyTeamEvent, teamConsoleFromSnapshot } from '../features/team-runtime/teamEventReducer';
import { EMPTY_TEAM_CONSOLE, type TeamConsoleState } from '../types/teamRuntime';
import { useAppStore } from '../stores/appStore';

export type { ChatRunPlanState, ChatRunPlanStep, DisplaySnapshot } from '../types/runState';

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
  /** 与单 Agent DisplaySnapshot 完全隔离的 Team Console 状态。 */
  teamConsole: TeamConsoleState;
  /** RunState 投影出的 Plan Mode 状态 */
  plans: ChatRunPlanState[];
  /** 单 Agent 主展示快照 */
  displaySnapshot: DisplaySnapshot;
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
  subagentName?: string;
  subagentInvocationId?: string;
  subagentStatus?: string;
  subagentTimeline?: SubagentTimelineItem[];
  subagentSummary?: string;
  subagentError?: string;
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

function normalizeToolDisplayName(name: string): string {
  const value = String(name || 'tool');
  const parts = value.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return value;
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

/** 人机交互请求 */
export interface HitlRequestInfo {
  interactionId: string;
  type: string;
  sessionId: string;
  agentId: string;
  agentName: string;
  runId: string;
  scope: 'leader';
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
    externalResults?: Record<string, string>,
    rememberApproval?: boolean,
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
  projectId?: string;
  agentId?: string;
  agentName?: string;
  mode?: 'agent' | 'team';
  systemPrompt?: string;
  model?: string;
  sessionId?: string;
  displayMessage?: string;
  skillIds?: string[];
  deepThinking?: boolean;
  sandbox?: boolean;
  images?: Array<{
    name: string;
    media_type: string;
    data: string;
  }>;
  files?: Array<{
    name: string;
    path: string;
    size: number;
    media_type: string;
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
  teamConsole: EMPTY_TEAM_CONSOLE,
  plans: [],
  displaySnapshot: createEmptyDisplaySnapshot(),
  hitlRequests: [],
  clarificationRequests: [],
  modelName: '',
  tokenUsage: null,
  error: null,
};

const WAITING_RUN_STATUSES = new Set([
  'waiting',
  'waiting_user',
  'waiting_permission',
  'waiting_external',
]);

function hasPendingUserInteraction(state: StreamState): boolean {
  return (
    state.hitlRequests.length > 0 ||
    state.clarificationRequests.length > 0 ||
    state.plans.some((plan) => plan.status === 'waiting')
  );
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

function failStreamWithError(state: StreamState, error: string): StreamState {
  return {
    ...state,
    isStreaming: false,
    error,
    toolCalls: markActiveToolCallsFailed(state.toolCalls),
    runCompletedAt: state.runCompletedAt || new Date().toISOString(),
  };
}

function streamStateFromRunState(snapshot: ChatRunStateSnapshot): StreamState {
  const runStatus = String(snapshot.status || '');
  const displaySnapshot = normalizeDisplaySnapshot(snapshot.displaySnapshot);
  const displayState = deriveStateFromDisplaySnapshot(
    displaySnapshot,
    String(snapshot.runId || ''),
    String(snapshot.sessionId || ''),
  );
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
    textContent: displayState.finalAnswerContent,
    reasoningContent: displayState.reasoningContent,
    finalAnswerContent: displayState.finalAnswerContent,
    thinkingContent: displayState.thinkingContent,
    toolCalls: displayState.toolCalls.map((tool) => ({
      ...tool,
      status: normalizeToolStatusForRun(tool.status, runStatus),
    })),
    teamConsole: teamConsoleFromSnapshot(snapshot.teamSnapshot),
    skillCalls: displayState.skillCalls,
    modelName: String(snapshot.modelName || ''),
    tokenUsage: snapshot.tokenUsage || null,
    plans: displayState.plans,
    displaySnapshot,
    hitlRequests: displayState.hitlRequests.length > 0 ? displayState.hitlRequests : hitlRequests,
    clarificationRequests: displayState.clarificationRequests.length > 0 ? displayState.clarificationRequests : clarificationRequests,
    error: displayState.error || String(snapshot.error || ''),
  };
}

function applyRunStatePatch(state: StreamState, patch: ChatRunStatePatch): StreamState {
  const patchKind = patch.kind || 'snapshot_meta';
  const nextStatus = String(patch.status || '');
  const isTerminal = ['completed', 'failed', 'cancelled'].includes(nextStatus);
  const isWaiting = WAITING_RUN_STATUSES.has(nextStatus);
  const displaySnapshot = patchKind === 'display_event' && patch.displayEvent
    ? applyDisplayEvent(state.displaySnapshot, patch.displayEvent)
    : state.displaySnapshot;
  const teamConsole = patchKind === 'team_event' && patch.teamEvent
    ? applyTeamEvent(state.teamConsole, patch.teamEvent)
    : state.teamConsole;
  const hasDisplaySnapshot = displaySnapshot.blocks.length > 0 || displaySnapshot.actors.length > 0;
  const displayState = deriveStateFromDisplaySnapshot(
    displaySnapshot,
    String(patch.runId || state.currentRunId),
    String(patch.sessionId || ''),
  );
  let toolCalls = hasDisplaySnapshot ? displayState.toolCalls : state.toolCalls;
  if (isTerminal) {
    toolCalls = markActiveToolCallsFailed(toolCalls);
  }
  let hitlRequests = state.hitlRequests;
  let clarificationRequests = state.clarificationRequests;
  if (patchKind === 'approval_resolved' && patch.resolvedApprovalId) {
    hitlRequests = hitlRequests.filter((item) => item.interactionId !== patch.resolvedApprovalId);
    clarificationRequests = clarificationRequests.filter((item) => item.id !== patch.resolvedApprovalId);
  }
  return {
    ...state,
    currentRunId: String(patch.runId || state.currentRunId),
    runCompletedAt: String(patch.completedAt || state.runCompletedAt || ''),
    textContent: hasDisplaySnapshot ? displayState.finalAnswerContent : state.textContent,
    finalAnswerContent: hasDisplaySnapshot ? displayState.finalAnswerContent : state.finalAnswerContent,
    reasoningContent: hasDisplaySnapshot ? displayState.reasoningContent : state.reasoningContent,
    thinkingContent: hasDisplaySnapshot ? displayState.thinkingContent : state.thinkingContent,
    toolCalls,
    teamConsole,
    skillCalls: hasDisplaySnapshot
      ? displayState.skillCalls
      : state.skillCalls,
    modelName: patchKind === 'model_usage' && typeof patch.modelName === 'string'
      ? patch.modelName
      : state.modelName,
    tokenUsage: patchKind === 'model_usage' && patch.tokenUsage !== undefined ? patch.tokenUsage : state.tokenUsage,
    hitlRequests: hasDisplaySnapshot ? displayState.hitlRequests : hitlRequests,
    clarificationRequests: hasDisplaySnapshot ? displayState.clarificationRequests : clarificationRequests,
    plans: hasDisplaySnapshot
      ? displayState.plans
      : state.plans,
    displaySnapshot,
    isStreaming: isTerminal || isWaiting ? false : state.isStreaming,
    error: hasDisplaySnapshot
      ? displayState.error || (patchKind === 'error' ? String(patch.error || state.error || '') : String(state.error || ''))
      : patchKind === 'error' ? String(patch.error || state.error || '') : String(state.error || ''),
  };
}

interface DisplayDerivedState {
  reasoningContent: string;
  thinkingContent: string;
  finalAnswerContent: string;
  toolCalls: ToolCallInfo[];
  skillCalls: SkillCallInfo[];
  plans: ChatRunPlanState[];
  hitlRequests: HitlRequestInfo[];
  clarificationRequests: ClarificationRequestInfo[];
  error: string;
}

function deriveStateFromDisplaySnapshot(
  snapshot: DisplaySnapshot,
  runId: string,
  sessionId: string,
): DisplayDerivedState {
  const processItems = snapshot.actors.flatMap((actor) => actor.items);
  const reasoningContent = processItems
    .filter((item) => item.event === DISPLAY_EVENTS.REASONING_DELTA || item.targetId === 'reasoning')
    .map((item) => pickDisplayString(item.data, ['text', 'content', 'delta']))
    .filter(Boolean)
    .join('\n\n');
  const thinkingContent = processItems
    .filter((item) => item.event === DISPLAY_EVENTS.THINKING_DELTA || item.targetId === 'thinking')
    .map((item) => pickDisplayString(item.data, ['text', 'content', 'delta']))
    .filter(Boolean)
    .join('\n\n');
  const toolCalls = processItems
    .filter((item) => item.event.startsWith('tool.') || item.targetId.startsWith('tool'))
    .map(toolCallFromDisplayItem);
  const skillCalls = processItems
    .filter((item) => item.event.startsWith('skill.') || item.targetId.startsWith('skill'))
    .map(skillCallFromDisplayItem);
  const answerBlock = snapshot.blocks.find((block) => block.block === DISPLAY_BLOCKS.ANSWER);
  const feedbackBlock = snapshot.blocks.find((block) => block.block === DISPLAY_BLOCKS.FEEDBACK);
  const errorBlock = snapshot.blocks.find((block) => block.block === DISPLAY_BLOCKS.ERROR);
  const approvals = feedbackBlock ? approvalsFromDisplayBlock(feedbackBlock) : [];

  return {
    reasoningContent,
    thinkingContent,
    finalAnswerContent: answerBlock ? pickDisplayString(answerBlock.data, ['content', 'text']) : '',
    toolCalls,
    skillCalls,
    plans: snapshot.blocks
      .filter((block) => block.block === DISPLAY_BLOCKS.PLAN)
      .map(planStateFromDisplayBlock)
      .filter((plan) => plan.planId),
    hitlRequests: approvals
      .filter((approval) => approval.status === 'waiting' && !isClarificationApproval(approval))
      .map((approval) => hitlRequestFromApprovalContext(runId, sessionId, approval)),
    clarificationRequests: approvals
      .filter((approval) => approval.status === 'waiting' && isClarificationApproval(approval))
      .map((approval) => clarificationRequestFromRunStateApproval(approval)),
    error: errorBlock ? pickDisplayString(errorBlock.data, ['message', 'error']) : '',
  };
}

function pickDisplayString(data: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = data[key];
    if (value === undefined || value === null) continue;
    const text = String(value);
    if (text) return text;
  }
  return '';
}

function toolCallFromDisplayItem(item: DisplayBlock['items'][number]): ToolCallInfo {
  return {
    id: item.targetId || item.id || createId(),
    name: normalizeToolDisplayName(pickDisplayString(item.data, ['name', 'toolName', 'rawName']) || 'tool'),
    args: pickDisplayString(item.data, ['inputPreview', 'input', 'args']),
    result: pickDisplayString(item.data, ['outputPreview', 'output', 'result', 'error']),
    status: normalizeToolStatus(item.status || item.data.status),
    ...subagentToolDataFromDisplayItem(item),
  };
}

function skillCallFromDisplayItem(item: DisplayBlock['items'][number]): SkillCallInfo {
  const status = String(item.status || item.data.status || 'running');
  return {
    id: item.targetId || item.id || createId(),
    name: pickDisplayString(item.data, ['name']) || 'Skill',
    description: pickDisplayString(item.data, ['description']),
    trigger: pickDisplayString(item.data, ['trigger']),
    content: pickDisplayString(item.data, ['content']),
    resourcePath: pickDisplayString(item.data, ['resourcePath']),
    resources: Array.isArray(item.data.resources) ? item.data.resources.map((resource) => String(resource)) : [],
    status: (
      status === 'completed' || status === 'failed' || status === 'running'
        ? status
        : 'running'
    ) as SkillCallInfo['status'],
  };
}

function planStateFromDisplayBlock(block: DisplayBlock): ChatRunPlanState {
  return {
    planId: pickDisplayString(block.data, ['planId']) || block.id.replace(/^plan-/, ''),
    planFile: pickDisplayString(block.data, ['planFile']),
    status: String(block.status || block.data.status || 'running'),
    markdown: pickDisplayString(block.data, ['markdown', 'content']),
    steps: Array.isArray(block.data.steps) ? block.data.steps.map(normalizeRunStatePlanStep) : [],
    updatedAt: block.updatedAt,
  };
}

function approvalsFromDisplayBlock(block: DisplayBlock): RunStateApprovalSummary[] {
  const approvalSource = block.data.approval ?? block.data.request ?? block.data.current;
  if (approvalSource) {
    const approval = normalizeDisplayApproval(approvalSource, block);
    return approval ? [approval] : [];
  }
  if (Array.isArray(block.data.approvals)) {
    return block.data.approvals.flatMap((item) => {
      const approval = normalizeDisplayApproval(item, block);
      return approval ? [approval] : [];
    });
  }
  const approval = normalizeDisplayApproval(block.data, block);
  return approval ? [approval] : [];
}

function normalizeDisplayApproval(source: unknown, block: DisplayBlock): RunStateApprovalSummary | null {
  if (!source || typeof source !== 'object') return null;
  const record = source as Record<string, unknown>;
  const interactionId = String(record.interactionId || record.id || block.id || '');
  if (!interactionId) return null;
  return {
    interactionId,
    kind: record.kind ? String(record.kind) : undefined,
    type: String(record.type || 'tool_approval'),
    status: String(record.status || block.status || 'waiting'),
    agentId: String(record.agentId || ''),
    agentName: String(record.agentName || 'Agent'),
    toolCalls: Array.isArray(record.toolCalls)
      ? record.toolCalls.map((toolCall) => normalizeApprovalToolCall(toolCall))
      : [],
    title: record.title ? String(record.title) : undefined,
    description: record.description ? String(record.description) : undefined,
    questions: Array.isArray(record.questions)
      ? record.questions.flatMap((question) => (
        question && typeof question === 'object' ? [question as Record<string, unknown>] : []
      ))
      : undefined,
    externalResultsPreview: normalizeExternalResultsPreview(record.externalResultsPreview),
    createdAt: String(record.createdAt || block.updatedAt || new Date().toISOString()),
  };
}

function normalizeExternalResultsPreview(value: unknown): Record<string, string> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => [key, String(item || '')])
      .filter(([, item]) => item),
  );
}

function normalizeApprovalToolCall(toolCall: unknown): RunStateApprovalSummary['toolCalls'][number] {
  if (!toolCall || typeof toolCall !== 'object') {
    return {};
  }
  const record = toolCall as Record<string, unknown>;
  return {
    id: record.id ? String(record.id) : undefined,
    name: record.name ? String(record.name) : undefined,
    input: record.input ? String(record.input) : undefined,
    rawName: record.rawName ? String(record.rawName) : undefined,
  };
}

function hitlRequestFromApprovalContext(
  runId: string,
  sessionId: string,
  approval: RunStateApprovalSummary,
): HitlRequestInfo {
  return {
    interactionId: String(approval.interactionId || ''),
    type: String(approval.type || 'tool_approval'),
    sessionId,
    agentId: String(approval.agentId || ''),
    agentName: String(approval.agentName || ''),
    runId,
    scope: 'leader',
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

/**
 * useEventStream Hook
 *
 * 管理 SSE 事件流的完整生命周期：发送请求 → 接收事件 → 更新状态
 */
export function useEventStream(): UseEventStreamReturn {
  const activeProjectId = useAppStore((storeState) => storeState.activeProjectId);
  const [state, setState] = useState<StreamState>(INITIAL_STATE);
  const abortControllerRef = useRef<AbortController | null>(null);

  /** 处理 RunState 协议事件，Team 事件由独立的 teamEventReducer 投影。 */
  const handleEvent = useCallback((event: SSEEvent) => {
    if (event.type === 'RUN_STATE_PATCH') {
      const patch = event.data as unknown as ChatRunStatePatch;
      if (patch.kind === 'notice' && patch.notice?.type === 'MEMORY_SKILL_GENERATING') {
        notifySuccess(patch.notice.message || 'Skills 生成中');
      }
    }
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
  }, []);

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
  }, [handleEvent]);

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
        const params = new URLSearchParams({ projectId: activeProjectId });
        await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/cancel?${params.toString()}`, {
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
  }, [activeProjectId, state.currentRunId]);

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
      const params = new URLSearchParams({ projectId: activeProjectId });
      const response = await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/state/stream?${params.toString()}`, {
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
  }, [activeProjectId, readEventStream]);

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
            project_id: options?.projectId,
            display_message: options?.displayMessage,
            agent_id: options?.agentId,
            agent_name: options?.agentName,
            mode: options?.mode || 'agent',
            system_prompt: options?.systemPrompt,
            model: options?.model,
            session_id: options?.sessionId,
            skill_ids: options?.skillIds,
            deep_thinking: options?.deepThinking,
            sandbox: options?.sandbox,
            images: options?.images,
            files: options?.files,
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
      externalResults: Record<string, string> = {},
      rememberApproval = false,
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
        clarificationRequests: prev.clarificationRequests.filter((item) => item.id !== interactionId),
      }));

      try {
        const response = await fetch('/api/chat/resume/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            interactionId,
            action,
            externalResults,
            rememberApproval,
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

  return { state, sendMessage, resumeInteraction, subscribeRun, abort, reset };
}
