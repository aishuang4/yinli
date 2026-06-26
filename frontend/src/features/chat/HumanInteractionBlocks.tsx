import { useState } from 'react';
import { AlertCircle, Wrench } from 'lucide-react';
import { Button, Tag } from '@agentscope-ai/design';
import type { ClarificationQuestionInfo, ClarificationRequestInfo, HitlRequestInfo } from '../../hooks/useEventStream';

export function HitlApprovalBlock({
  request,
  onResolve,
}: {
  request: HitlRequestInfo;
  onResolve: (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState<'approve' | 'reject' | null>(null);
  const isExternal = request.type === 'REQUIRE_EXTERNAL_EXECUTION';
  const askUserToolCall = request.toolCalls.find((toolCall) => isAskUserToolName(toolCall.rawName || toolCall.name));
  const askUserRequest = askUserToolCall ? buildClarificationRequestFromAskUser(askUserToolCall) : null;

  const handleResolve = async (action: 'approve' | 'reject') => {
    setSubmitting(action);
    try {
      await onResolve(request.interactionId, action);
    } finally {
      setSubmitting(null);
    }
  };

  if (askUserToolCall && askUserRequest) {
    return (
      <ClarificationBlock
        request={askUserRequest}
        onSubmit={async (content) => {
          setSubmitting('approve');
          try {
            await onResolve(request.interactionId, 'approve', {
              [askUserToolCall.id]: content,
            });
          } finally {
            setSubmitting(null);
          }
        }}
        onCancel={async () => {
          setSubmitting('reject');
          try {
            await onResolve(request.interactionId, 'reject');
          } finally {
            setSubmitting(null);
          }
        }}
        submittingOverride={submitting === 'approve'}
        cancelSubmittingOverride={submitting === 'reject'}
      />
    );
  }

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <AlertCircle size={14} color="var(--color-warning)" />
          <span>{isExternal ? '等待外部执行结果' : '等待人工确认'}</span>
        </div>
        <Tag size="small" color="transparent">
          {request.scope === 'worker' ? 'Worker' : 'Leader'} · {request.agentName}
        </Tag>
      </div>
      <div className="space-y-2 px-3 pb-3">
        {request.toolCalls.map((toolCall) => (
            <div key={toolCall.id} className="rounded-md bg-[var(--color-bg-primary)] p-3">
            <div className="mb-2 flex items-center gap-2">
              <Wrench size={13} color="var(--color-accent)" />
              <span className="text-sm font-medium text-[var(--color-text-primary)]">
                {toolCall.name}
              </span>
            </div>
            {toolCall.input && (
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--color-bg-tertiary)] p-2 text-xs text-[var(--color-text-secondary)]">
                {formatToolInput(toolCall.input)}
              </pre>
            )}
          </div>
        ))}
        <div className="flex justify-end gap-2">
          <Button
            danger
            disabled={Boolean(submitting)}
            loading={submitting === 'reject'}
            onClick={() => handleResolve('reject')}
          >
            拒绝
          </Button>
          <Button
            type="primary"
            disabled={Boolean(submitting)}
            loading={submitting === 'approve'}
            onClick={() => handleResolve('approve')}
          >
            批准
          </Button>
        </div>
      </div>
    </div>
  );
}

function isAskUserToolName(name: string) {
  const normalized = name.replace(/\s+/g, '').toLowerCase();
  return normalized === 'askuser' || normalized.endsWith('_askuser');
}

export function isAskUserHitlRequest(request: HitlRequestInfo): boolean {
  return request.toolCalls.some((toolCall) => isAskUserToolName(toolCall.rawName || toolCall.name));
}

function buildClarificationRequestFromAskUser(toolCall: HitlRequestInfo['toolCalls'][number]): ClarificationRequestInfo {
  let raw: Record<string, unknown>;
  try {
    raw = JSON.parse(toolCall.input || '{}') as Record<string, unknown>;
  } catch {
    raw = {};
  }

  const questions = Array.isArray(raw.questions) ? raw.questions : [];
  const parsedQuestions = questions.flatMap((item, index) => {
    if (!item || typeof item !== 'object') return [];
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
      required: question.required !== false,
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

export function HitlDock({
  requests,
  onResolve,
}: {
  requests: HitlRequestInfo[];
  onResolve: (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;
}) {
  if (requests.length === 0) return null;

  return (
    <div className="mx-auto mb-3 max-w-4xl rounded-xl border border-[var(--color-warning)] bg-[var(--color-bg-primary)] p-3 shadow-lg">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <AlertCircle size={15} color="var(--color-warning)" />
          <span>需要人工审批</span>
          <Tag size="small" color="warning">
            {requests.length}
          </Tag>
        </div>
        <div className="text-xs text-[var(--color-text-tertiary)]">
          审批完成后 Agent 会继续执行
        </div>
      </div>
      <div className="max-h-[320px] space-y-2 overflow-y-auto pr-1">
        {requests.map((request) => (
          <HitlApprovalBlock
            key={request.interactionId}
            request={request}
            onResolve={onResolve}
          />
        ))}
      </div>
    </div>
  );
}

export function ClarificationBlock({
  request,
  onSubmit,
  onCancel,
  submittingOverride,
  cancelSubmittingOverride,
  readOnly = false,
}: {
  request: ClarificationRequestInfo;
  onSubmit: (content: string, displayContent?: string) => Promise<void>;
  onCancel?: () => Promise<void>;
  submittingOverride?: boolean;
  cancelSubmittingOverride?: boolean;
  readOnly?: boolean;
}) {
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({});
  const [submitting, setSubmitting] = useState(false);

  const setAnswer = (id: string, value: string | string[]) => {
    setAnswers((current) => ({ ...current, [id]: value }));
  };

  const toggleMulti = (id: string, option: string) => {
    const current = Array.isArray(answers[id]) ? answers[id] as string[] : [];
    setAnswer(
      id,
      current.includes(option)
        ? current.filter((item) => item !== option)
        : [...current, option],
    );
  };

  const handleSubmit = async () => {
    const missing = request.questions.some((question) => {
      if (!question.required) return false;
      const value = answers[question.id];
      return Array.isArray(value) ? value.length === 0 : !String(value || '').trim();
    });
    if (missing) return;

    const entries = request.questions.map((question) => {
      const value = answers[question.id];
      const answer = Array.isArray(value) ? value.join('、') : String(value || '').trim();
      return {
        label: question.label,
        answer: answer || '未填写',
      };
    });
    const modelLines = entries.map((entry) => `- ${entry.label}: ${entry.answer}`);
    const displayText = entries.map((entry) => entry.answer).join('；');
    setSubmitting(true);
    try {
      await onSubmit(
        `针对上一个补充信息请求，用户回答如下：\n${modelLines.join('\n')}`,
        displayText,
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded-lg bg-[var(--color-bg-secondary)] p-3 shadow-sm">
      <div className="text-sm font-medium text-[var(--color-text-primary)]">
        {request.title}
      </div>
      {readOnly && (
        <div className="mt-2">
          <Tag size="small" color="green">
            已提交补充信息
          </Tag>
        </div>
      )}
      {request.description && (
        <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
          {request.description}
        </div>
      )}
      <div className="mt-3 space-y-3">
        {request.questions.map((question) => {
          const options = question.options || [];
          return (
          <div key={question.id}>
            <div className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">
              {question.label}
            </div>
            {readOnly ? (
              <div className="rounded-md bg-[var(--color-bg-primary)] px-3 py-2 text-xs text-[var(--color-text-tertiary)]">
                {options.length > 0 ? `可选项：${options.join('、')}` : '文本补充题，答案见下一条用户消息'}
              </div>
            ) : question.type === 'choice' && (
              <div className="flex flex-wrap gap-2">
                {options.map((option) => (
                  <Button
                    key={option}
                    size="small"
                    type={answers[question.id] === option ? 'primary' : 'default'}
                    onClick={() => setAnswer(question.id, option)}
                  >
                    {option}
                  </Button>
                ))}
              </div>
            )}
            {!readOnly && question.type === 'multi_choice' && (
              <div className="flex flex-wrap gap-2">
                {options.map((option) => {
                  const selected = Array.isArray(answers[question.id]) && (answers[question.id] as string[]).includes(option);
                  return (
                    <label
                      key={option}
                      className={`flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1 text-xs transition ${
                        selected
                          ? 'border-[var(--color-accent)] bg-[var(--color-accent)] text-[var(--color-text-inverse)]'
                          : 'border-[var(--color-border)] bg-[var(--color-bg-primary)] text-[var(--color-text-secondary)]'
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="h-3 w-3"
                        checked={selected}
                        onChange={() => toggleMulti(question.id, option)}
                      />
                      {option}
                    </label>
                  );
                })}
              </div>
            )}
            {!readOnly && question.type === 'text' && (
              <textarea
                value={String(answers[question.id] || '')}
                onChange={(event) => setAnswer(question.id, event.target.value)}
                placeholder={question.placeholder || '请输入补充信息'}
                rows={3}
                className="w-full resize-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2 text-sm text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)]"
              />
            )}
          </div>
          );
        })}
      </div>
      {!readOnly && (
      <div className="mt-3 flex justify-end gap-2">
        {onCancel && (
          <Button
            danger
            loading={cancelSubmittingOverride}
            disabled={submitting || submittingOverride}
            onClick={onCancel}
          >
            取消请求
          </Button>
        )}
        <Button type="primary" loading={submitting || submittingOverride} onClick={handleSubmit}>
          提交补充信息
        </Button>
      </div>
      )}
    </div>
  );
}

function formatToolInput(input: string) {
  try {
    return JSON.stringify(JSON.parse(input), null, 2);
  } catch {
    return input;
  }
}
