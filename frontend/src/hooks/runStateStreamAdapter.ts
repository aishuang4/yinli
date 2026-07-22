/** RunState 与前端 StreamState 之间的纯转换。 */

import { DISPLAY_BLOCKS, DISPLAY_EVENTS, type DisplayBlock, type DisplaySnapshot } from '../types/displayEvents';
import type {
  ChatRunPlanState,
  ChatRunPlanStep,
  ChatRunStatePatch,
  ChatRunStateSnapshot,
  RunStateApprovalSummary,
} from '../types/runState';
import { applyDisplayEvent, createEmptyDisplaySnapshot, normalizeDisplaySnapshot } from '../utils/displayEventReducer';
import { createId } from '../utils/id';
import { subagentToolDataFromDisplayItem } from '../features/chat/subagentToolData';
import { applyTeamEvent, teamConsoleFromSnapshot } from '../features/team-runtime/teamEventReducer';
import { EMPTY_TEAM_CONSOLE } from '../types/teamRuntime';
import {
  normalizeToolDisplayName,
  normalizeToolStatus,
  type ClarificationQuestionInfo,
  type ClarificationRequestInfo,
  type HitlRequestInfo,
  type SkillCallInfo,
  type StreamState,
  type ToolCallInfo,
} from './eventStreamTypes';


/** 初始状态 */
export const INITIAL_STATE: StreamState = {
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

export function completeStreamIfNoPendingUserInteraction(state: StreamState): StreamState {
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

export function failStreamWithError(state: StreamState, error: string): StreamState {
  return {
    ...state,
    isStreaming: false,
    error,
    toolCalls: markActiveToolCallsFailed(state.toolCalls),
    runCompletedAt: state.runCompletedAt || new Date().toISOString(),
  };
}

export function streamStateFromRunState(snapshot: ChatRunStateSnapshot): StreamState {
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

export function applyRunStatePatch(state: StreamState, patch: ChatRunStatePatch): StreamState {
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
