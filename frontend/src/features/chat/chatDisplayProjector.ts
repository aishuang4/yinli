import { DISPLAY_BLOCKS, DISPLAY_EVENTS, type DisplayActor, type DisplayBlock, type DisplayItem } from '../../types/displayEvents';
import type {
  ChatRunPlanStep,
  ClarificationQuestionInfo,
  ClarificationRequestInfo,
  HitlRequestInfo,
  SkillCallInfo,
  StreamState,
  TeamActorInfo,
  ToolCallInfo,
} from '../../hooks/useEventStream';
import type { RunStateApprovalSummary } from '../../types/runState';
import { normalizeDisplaySnapshot } from '../../utils/displayEventReducer';
import { createId } from '../../utils/id';
import type { LocalMessage } from './types';
import type {
  ChatDisplayBlock,
  ChatDisplayMessage,
  ChatDisplayMessageInput,
  ChatDisplayStatus,
  ChatFinalAnswerBlock,
  ChatGeneratedFilesBlock,
  ChatHumanFeedbackBlock,
  ChatPlanBlock,
  ChatProcessBlock,
  ChatProcessItem,
} from './chatDisplayTypes';

export function buildLiveChatDisplayMessage(
  state: StreamState,
): ChatDisplayMessage {
  return buildChatDisplayMessage({
    displaySnapshot: state.displaySnapshot,
    runId: state.currentRunId,
    sessionId: '',
    status: inferRunStatus(state),
    isStreaming: state.isStreaming,
    startedAt: state.runStartedAt,
    completedAt: state.runCompletedAt,
    modelName: state.modelName,
    tokenUsage: state.tokenUsage,
  });
}

export function buildStoredChatDisplayMessage(message: LocalMessage): ChatDisplayMessage {
  return buildChatDisplayMessage({
    displaySnapshot: message.displaySnapshot,
    runId: '',
    sessionId: '',
    status: message.error ? 'failed' : 'completed',
    isStreaming: false,
    startedAt: message.processStartedAt || '',
    completedAt: message.processCompletedAt || '',
    modelName: message.modelName || '',
    tokenUsage: message.tokenUsage || null,
    readOnlyClarification: Boolean(message.clarificationResolved),
    fallbackAnswer: message.content,
  });
}

export function buildChatDisplayMessage(input: ChatDisplayMessageInput): ChatDisplayMessage {
  const snapshot = normalizeDisplaySnapshot(input.displaySnapshot);
  const blocks = buildDisplayBlocks(snapshot.blocks, input);
  const finalAnswerBlock = blocks.find((block): block is ChatFinalAnswerBlock => block.type === 'final_answer');
  return {
    id: input.runId || `display-${input.startedAt || 'message'}`,
    role: 'assistant',
    status: input.status,
    isStreaming: input.isStreaming,
    startedAt: input.startedAt,
    completedAt: input.completedAt,
    snapshot,
    blocks,
    finalAnswer: finalAnswerBlock?.content || '',
    modelName: input.modelName,
    tokenUsage: input.tokenUsage,
  };
}

function buildDisplayBlocks(blocks: DisplayBlock[], input: ChatDisplayMessageInput): ChatDisplayBlock[] {
  const snapshot = normalizeDisplaySnapshot(input.displaySnapshot);
  const actorBlocks = buildActorDisplayBlocks(snapshot.actors, snapshot.mode, input);
  const hasActorProcessBlock = actorBlocks.some((block) => block.type === 'process' && block.items.length > 0);
  const displayBlocks = [
    ...actorBlocks,
    ...blocks
    .filter((block) => !(hasActorProcessBlock && block.block === DISPLAY_BLOCKS.PROCESS))
    .map((block) => blockFromDisplayBlock(block, input))
    .filter((block): block is ChatDisplayBlock => Boolean(block))
    .filter(isVisibleBlock),
  ];

  if (displayBlocks.length === 0 && input.fallbackAnswer?.trim()) {
    return [{
      id: 'final-answer',
      type: 'final_answer',
      status: input.status,
      content: input.fallbackAnswer,
      started: true,
    }];
  }

  return displayBlocks;
}

function buildActorDisplayBlocks(
  actors: DisplayActor[],
  mode: string,
  input: ChatDisplayMessageInput,
): ChatDisplayBlock[] {
  if (mode === 'team') {
    const teamActors = actors.map(teamActorFromDisplayActor);
    return teamActors.length > 0
      ? [{
        id: 'team',
        type: 'team',
        status: input.status,
        actors: teamActors,
        updatedAt: actors.reduce((latest, actor) => actor.updatedAt || latest, ''),
      }]
      : [];
  }

  const actor = actors[0];
  if (!actor || actor.items.length === 0) return [];
  return [processBlockFromActor(actor, input)];
}

function processBlockFromActor(actor: DisplayActor, input: ChatDisplayMessageInput): ChatProcessBlock {
  const items = normalizeProcessItems(
    actor.items
      .map(processItemFromDisplayItem)
      .filter((item): item is ChatProcessItem => Boolean(item)),
  );
  return {
    id: actor.id || 'process',
    type: 'process',
    status: actor.status,
    startedAt: input.startedAt || actor.createdAt,
    completedAt: input.completedAt || actor.completedAt,
    updatedAt: actor.updatedAt,
    items,
  };
}

function teamActorFromDisplayActor(actor: DisplayActor): TeamActorInfo {
  return {
    id: actor.activityId || actor.id,
    agentId: actor.agentId,
    agentName: actor.agentName,
    role: actor.role,
    status: normalizeTeamActorStatus(actor.status),
    task: actor.task,
    assignment: actor.assignment,
    expectedOutput: actor.expectedOutput,
    constraints: actor.constraints,
    detail: actor.detail,
    output: actor.output,
    report: actor.report,
    skippedReason: actor.skippedReason,
    reportReviewStatus: actor.reportReviewStatus,
    reportReviewNotes: actor.reportReviewNotes,
    reportReviewGaps: actor.reportReviewGaps,
    thinking: actor.items
      .filter((item) => item.event === DISPLAY_EVENTS.THINKING_DELTA)
      .map((item) => pickDisplayString(item.data, ['text', 'content', 'delta']))
      .join('\n\n'),
    currentEvent: actor.detail,
    items: actor.items,
    toolCalls: actor.items
      .filter((item) => item.event.startsWith('tool.') || item.targetId.startsWith('tool'))
      .map(toolCallFromDisplayItem),
    executionMode: actor.executionMode,
    dependsOn: actor.dependsOn,
    waitReason: actor.waitReason,
    dependencyOutputCount: actor.dependencyOutputCount,
    leaderContextCount: actor.leaderContextCount,
    createdAt: actor.createdAt,
  };
}

function normalizeTeamActorStatus(status: string): TeamActorInfo['status'] {
  if (
    status === 'running' ||
    status === 'waiting' ||
    status === 'completed' ||
    status === 'failed' ||
    status === 'skipped'
  ) {
    return status;
  }
  return 'pending';
}

function blockFromDisplayBlock(
  block: DisplayBlock,
  input: ChatDisplayMessageInput,
): ChatDisplayBlock | null {
  if (block.block === DISPLAY_BLOCKS.PROCESS) {
    return processBlockFromDisplayBlock(block, input);
  }
  if (block.block === DISPLAY_BLOCKS.PLAN) {
    return planBlockFromDisplayBlock(block);
  }
  if (block.block === DISPLAY_BLOCKS.FEEDBACK) {
    return feedbackBlockFromDisplayBlock(block, input);
  }
  if (block.block === DISPLAY_BLOCKS.ANSWER) {
    return answerBlockFromDisplayBlock(block);
  }
  if (block.block === DISPLAY_BLOCKS.FILES) {
    return filesBlockFromDisplayBlock(block);
  }
  if (block.block === DISPLAY_BLOCKS.ERROR) {
    return errorBlockFromDisplayBlock(block);
  }
  return null;
}

function processBlockFromDisplayBlock(block: DisplayBlock, input: ChatDisplayMessageInput): ChatProcessBlock | null {
  const items = normalizeProcessItems(
    block.items
      .map(processItemFromDisplayItem)
      .filter((item): item is ChatProcessItem => Boolean(item)),
  );
  if (items.length === 0) return null;
  return {
    id: block.id,
    type: 'process',
    status: block.status,
    startedAt: input.startedAt,
    completedAt: input.completedAt,
    updatedAt: block.updatedAt,
    items,
  };
}

function processItemFromDisplayItem(item: DisplayItem): ChatProcessItem | null {
  const status = String(item.status || 'running');
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
    const content = pickDisplayString(item.data, ['text', 'content', 'delta']);
    return content ? { id: item.id, type: 'reasoning', content, status, updatedAt: item.updatedAt } : null;
  }
  if (isThinkingDisplayItem(item)) {
    const content = pickDisplayString(item.data, ['text', 'content', 'delta']);
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

function normalizeProcessItems(items: ChatProcessItem[]): ChatProcessItem[] {
  return items.reduce<ChatProcessItem[]>((normalized, item) => {
    const previous = normalized[normalized.length - 1];
    if (previous && isTextProcessItem(previous) && isTextProcessItem(item) && previous.type === item.type) {
      normalized[normalized.length - 1] = {
        ...previous,
        status: item.status || previous.status,
        updatedAt: item.updatedAt || previous.updatedAt,
        content: mergeAdjacentTextContent(previous.content, item.content),
      };
      return normalized;
    }
    if (item.type === 'tool') {
      const toolKey = canonicalToolProcessKey(item);
      const existingIndex = normalized.findIndex((current) => (
        current.type === 'tool' && canonicalToolProcessKey(current) === toolKey
      ));
      if (existingIndex >= 0) {
        normalized[existingIndex] = mergeToolProcessItem(normalized[existingIndex], item);
        return normalized;
      }
    }
    normalized.push(item);
    return normalized;
  }, []);
}

function isTextProcessItem(
  item: ChatProcessItem,
): item is Extract<ChatProcessItem, { type: 'reasoning' | 'thinking' }> {
  return item.type === 'reasoning' || item.type === 'thinking';
}

function mergeAdjacentTextContent(current: string, next: string): string {
  if (!current) return next;
  if (!next) return current;
  if (current === next || current.endsWith(next)) return current;
  if (next.startsWith(current)) return next;
  return `${current}\n\n${next}`;
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

function planBlockFromDisplayBlock(block: DisplayBlock): ChatPlanBlock | null {
  const planId = pickDisplayString(block.data, ['planId']) || block.id.replace(/^plan-/, '');
  const markdown = pickDisplayString(block.data, ['markdown', 'content']);
  const steps = Array.isArray(block.data.steps) ? block.data.steps.map(normalizePlanStep) : [];
  if (!planId && !markdown && steps.length === 0) return null;
  const status = String(block.status || block.data.status || 'running');
  return {
    id: block.id,
    type: 'plan',
    status,
    planId,
    planFile: pickDisplayString(block.data, ['planFile']),
    markdown,
    steps,
    updatedAt: block.updatedAt,
  };
}

function feedbackBlockFromDisplayBlock(block: DisplayBlock, input: ChatDisplayMessageInput): ChatHumanFeedbackBlock | null {
  const approvals = approvalsFromDisplayBlock(block);
  const hitlRequests = approvals
    .filter((approval) => approval.status === 'waiting' && !isClarificationApproval(approval))
    .map((approval) => hitlRequestFromApproval(input, approval));
  const clarificationRequests = approvals
    .filter((approval) => approval.status === 'waiting' && isClarificationApproval(approval))
    .map(clarificationRequestFromApproval);
  if (hitlRequests.length === 0 && clarificationRequests.length === 0) return null;
  return {
    id: block.id,
    type: 'human_feedback',
    status: block.status || 'waiting',
    hitlRequests,
    clarificationRequests,
    readOnlyClarification: Boolean(input.readOnlyClarification),
  };
}

function answerBlockFromDisplayBlock(block: DisplayBlock): ChatFinalAnswerBlock | null {
  const content = pickDisplayString(block.data, ['content', 'text']);
  const started = Boolean(block.data.started || content);
  if (!started) return null;
  return {
    id: block.id,
    type: 'final_answer',
    status: block.status,
    content,
    started,
  };
}

function filesBlockFromDisplayBlock(block: DisplayBlock): ChatGeneratedFilesBlock | null {
  const files = Array.isArray(block.data.files)
    ? block.data.files.flatMap((item) => {
      if (!item || typeof item !== 'object') return [];
      const record = item as Record<string, unknown>;
      const path = String(record.path || '');
      return path
        ? [{
          path,
          name: String(record.name || path.split('/').pop() || 'file'),
          extension: String(record.extension || 'FILE'),
        }]
        : [];
    })
    : [];
  if (files.length === 0) return null;
  return {
    id: block.id,
    type: 'generated_files',
    status: block.status,
    files,
  };
}

function errorBlockFromDisplayBlock(block: DisplayBlock): ChatDisplayBlock | null {
  const message = pickDisplayString(block.data, ['message', 'error']);
  return message ? { id: block.id, type: 'error', message } : null;
}

function isVisibleBlock(block: ChatDisplayBlock): boolean {
  if (block.type === 'team') return block.actors.length > 0;
  if (block.type === 'process') return block.items.length > 0;
  if (block.type === 'plan') return Boolean(block.planId || block.markdown.trim() || block.steps.length);
  if (block.type === 'human_feedback') return block.hitlRequests.length > 0 || block.clarificationRequests.length > 0;
  if (block.type === 'final_answer') return block.started || Boolean(block.content.trim());
  if (block.type === 'generated_files') return block.files.length > 0;
  if (block.type === 'error') return Boolean(block.message.trim());
  return false;
}

function inferRunStatus(state: StreamState): ChatDisplayStatus {
  if (state.error) return 'failed';
  if (
    state.hitlRequests.length > 0 ||
    state.clarificationRequests.length > 0 ||
    state.plans.some((plan) => plan.status === 'waiting')
  ) return 'waiting';
  if (state.isStreaming) return 'running';
  if (state.runCompletedAt) return 'completed';
  return 'idle';
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

function normalizePlanStep(step: unknown): ChatRunPlanStep {
  if (!step || typeof step !== 'object') return {};
  const record = step as Record<string, unknown>;
  return {
    id: record.id ? String(record.id) : undefined,
    planId: record.planId ? String(record.planId) : undefined,
    stepIndex: typeof record.stepIndex === 'number' ? record.stepIndex : Number(record.stepIndex || 0),
    title: record.title ? String(record.title) : undefined,
    description: record.description ? String(record.description) : undefined,
    status: record.status ? String(record.status) : undefined,
    assignedAgentId: record.assignedAgentId ? String(record.assignedAgentId) : undefined,
    evidence: record.evidence ? String(record.evidence) : undefined,
    result: record.result ? String(record.result) : undefined,
    startedAt: record.startedAt ? String(record.startedAt) : undefined,
    completedAt: record.completedAt ? String(record.completedAt) : undefined,
    updatedAt: record.updatedAt ? String(record.updatedAt) : undefined,
  };
}

function toolCallFromDisplayItem(item: DisplayItem): ToolCallInfo {
  return {
    id: item.targetId || item.id || createId(),
    name: normalizeToolDisplayName(pickDisplayString(item.data, ['name', 'toolName', 'rawName']) || 'tool'),
    args: pickDisplayString(item.data, ['inputPreview', 'input', 'args']),
    result: pickDisplayString(item.data, ['outputPreview', 'output', 'result', 'error']),
    status: normalizeToolStatus(toolStatusFromDisplayItem(item)),
  };
}

function skillCallFromDisplayItem(item: DisplayItem): SkillCallInfo {
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
      ? record.toolCalls.map(normalizeApprovalToolCall)
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

function hitlRequestFromApproval(input: ChatDisplayMessageInput, approval: RunStateApprovalSummary): HitlRequestInfo {
  return {
    interactionId: String(approval.interactionId || ''),
    type: String(approval.type || 'tool_approval'),
    sessionId: input.sessionId,
    agentId: String(approval.agentId || ''),
    agentName: String(approval.agentName || ''),
    runId: input.runId,
    scope: 'leader',
    teamActivityId: null,
    replyId: '',
    toolCalls: (approval.toolCalls || []).map((toolCall: RunStateApprovalSummary['toolCalls'][number]) => ({
      id: String(toolCall.id || createId()),
      name: normalizeToolDisplayName(String(toolCall.name || 'tool')),
      input: String(toolCall.input || ''),
      rawName: toolCall.rawName ? String(toolCall.rawName) : undefined,
    })),
    createdAt: String(approval.createdAt || new Date().toISOString()),
  };
}

function clarificationRequestFromApproval(approval: RunStateApprovalSummary): ClarificationRequestInfo {
  return {
    id: String(approval.interactionId || ''),
    title: String(approval.title || '需要补充信息'),
    description: String(approval.description || ''),
    questions: (approval.questions || []).map((question: Record<string, unknown>, index: number) => {
      const rawType = String(question.type || 'text');
      const type = rawType === 'choice' || rawType === 'multi_choice' || rawType === 'text'
        ? rawType
        : 'text';
      return {
        id: String(question.id || `question-${index + 1}`),
        type: type as ClarificationQuestionInfo['type'],
        label: String(question.label || question.title || `问题 ${index + 1}`),
        options: Array.isArray(question.options) ? question.options.map((option: unknown) => String(option)) : [],
        placeholder: String(question.placeholder || ''),
        required: question.required === undefined ? true : Boolean(question.required),
      };
    }),
    createdAt: String(approval.createdAt || new Date().toISOString()),
  };
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

function normalizeToolDisplayName(name: string): string {
  const parts = name.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return name;
}

function toolStatusFromDisplayItem(item: DisplayItem): string {
  if (item.event === DISPLAY_EVENTS.TOOL_COMPLETED) return 'completed';
  if (item.event === DISPLAY_EVENTS.TOOL_FAILED) return 'failed';
  return String(item.status || item.data.status || 'calling');
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
