import type {
  ClarificationQuestionInfo,
  ClarificationRequestInfo,
  HitlRequestInfo,
} from '../../hooks/useEventStream';
import type { DisplayBlock, DisplaySnapshot } from '../runtime-stream/protocol';

type RecordData = Record<string, unknown>;

export interface PendingAskUserInteraction {
  hitlRequest: HitlRequestInfo;
  clarificationRequest: ClarificationRequestInfo;
  toolCallId: string;
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

export function isAskUserToolName(name: string): boolean {
  const normalized = name.replace(/\s+/g, '').toLowerCase();
  return normalized === 'askuser' || normalized.endsWith('_askuser');
}

export function isAskUserHitlRequest(request: HitlRequestInfo): boolean {
  return request.toolCalls.some((toolCall) => isAskUserToolName(toolCall.rawName || toolCall.name));
}

export function toHitlRequest(block: DisplayBlock<RecordData>): HitlRequestInfo | null {
  const interactionId = text(block.data.interactionId);
  if (!interactionId) return null;
  const rawToolCalls = Array.isArray(block.data.toolCalls) ? block.data.toolCalls : [];
  const rawScope = text(block.data.scope);
  const scope = rawScope === 'single' || rawScope === 'subagent' || rawScope === 'team'
    ? rawScope
    : 'leader';
  return {
    interactionId,
    type: text(block.data.type, text(block.data.kind, 'HITL_REQUIRED')),
    sessionId: text(block.data.sessionId),
    agentId: text(block.data.agentId),
    agentName: text(block.data.agentName, 'Agent'),
    runId: text(block.data.runId),
    scope,
    replyId: text(block.data.replyId),
    toolCalls: rawToolCalls.flatMap((item, index) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
      const value = item as RecordData;
      return [{
        id: text(value.id, `tool-${index + 1}`),
        name: text(value.name, 'Tool'),
        input: text(value.input),
        rawName: text(value.rawName) || undefined,
      }];
    }),
    createdAt: text(block.data.createdAt, new Date().toISOString()),
  };
}

export function buildClarificationRequestFromAskUser(
  toolCall: HitlRequestInfo['toolCalls'][number],
): ClarificationRequestInfo {
  let raw: Record<string, unknown>;
  try {
    raw = JSON.parse(toolCall.input || '{}') as Record<string, unknown>;
  } catch {
    raw = {};
  }

  const questions = Array.isArray(raw.questions) ? raw.questions : [];
  const parsedQuestions = questions.flatMap((item, index) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
    const question = item as Record<string, unknown>;
    const type = String(question.type || 'text');
    if (!['choice', 'multi_choice', 'text'].includes(type)) return [];
    const options = Array.isArray(question.options)
      ? question.options.map((option) => String(option)).filter(Boolean).slice(0, 5)
      : [];
    if ((type === 'choice' || type === 'multi_choice') && options.length < 2) return [];
    return [{
      id: String(question.id || `q${index + 1}`),
      type: type as ClarificationQuestionInfo['type'],
      label: String(question.label || question.question || `问题 ${index + 1}`),
      options,
      placeholder: String(question.placeholder || ''),
      required: question.required === true,
    }];
  }).slice(0, 3);

  return {
    id: `ask-user-${toolCall.id}`,
    title: String(raw.title || '需要补充信息'),
    description: String(raw.description || 'Agent 需要这些信息后才能继续执行。'),
    createdAt: new Date().toISOString(),
    questions: parsedQuestions.length > 0
      ? parsedQuestions
      : [{
        id: 'details',
        type: 'text',
        label: '请补充信息',
        options: [],
        placeholder: '输入后 Agent 会继续当前执行',
        required: true,
      }],
  };
}

export function selectPendingAskUserInteractions(
  snapshot: DisplaySnapshot,
): PendingAskUserInteraction[] {
  return snapshot.order.flatMap((id) => {
    const block = snapshot.blocksById[id];
    if (!block || block.kind !== 'approval' || block.phase !== 'open') return [];
    const hitlRequest = toHitlRequest(block as DisplayBlock<RecordData>);
    if (!hitlRequest) return [];
    const toolCall = hitlRequest.toolCalls.find((item) => (
      isAskUserToolName(item.rawName || item.name)
    ));
    if (!toolCall) return [];
    return [{
      hitlRequest,
      clarificationRequest: buildClarificationRequestFromAskUser(toolCall),
      toolCallId: toolCall.id,
    }];
  });
}
