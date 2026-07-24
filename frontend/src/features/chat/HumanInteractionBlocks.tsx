import { useState } from 'react';
import { AlertCircle, Wrench } from 'lucide-react';
import { Button, Checkbox, Tag } from '@agentscope-ai/design';
import type { ClarificationRequestInfo, HitlRequestInfo } from '../../hooks/useEventStream';

export function HitlApprovalBlock({
  request,
  onResolve,
}: {
  request: HitlRequestInfo;
  onResolve: (
    interactionId: string,
    action: 'approve' | 'reject',
    externalResults?: Record<string, string>,
    rememberApproval?: boolean,
  ) => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState<'approve' | 'reject' | null>(null);
  const [rememberApproval, setRememberApproval] = useState(false);
  const isExternal = request.type === 'REQUIRE_EXTERNAL_EXECUTION';
  const actorLabel = request.scope === 'subagent'
    ? 'Subagent'
    : request.scope === 'team'
      ? 'Team'
      : 'Agent';

  const handleResolve = async (action: 'approve' | 'reject') => {
    setSubmitting(action);
    try {
      await onResolve(request.interactionId, action, {}, action === 'approve' && rememberApproval);
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <AlertCircle size={14} color="var(--color-warning)" />
          <span>{isExternal ? '等待外部执行结果' : '等待人工确认'}</span>
        </div>
        <Tag size="small" color="transparent">
          {actorLabel} · {request.agentName}
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
        {!isExternal && (
          <Checkbox
            checked={rememberApproval}
            disabled={Boolean(submitting)}
            onChange={(event) => setRememberApproval(event.target.checked)}
          >
            本会话后续自动允许这些工具
          </Checkbox>
        )}
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

    const entries = request.questions.flatMap((question) => {
      const value = answers[question.id];
      const answer = Array.isArray(value) ? value.join('、') : String(value || '').trim();
      if (!answer) return [];
      return [{
        label: question.label,
        answer,
      }];
    });
    const modelLines = entries.map((entry) => `- ${entry.label}: ${entry.answer}`);
    const hasAnswers = entries.length > 0;
    const displayText = hasAnswers ? entries.map((entry) => entry.answer).join('；') : '已跳过选填项';
    setSubmitting(true);
    try {
      await onSubmit(
        hasAnswers
          ? `针对上一个补充信息请求，用户回答如下：\n${modelLines.join('\n')}`
          : '用户未提供选填信息，请基于现有信息继续执行。',
        displayText,
      );
    } finally {
      setSubmitting(false);
    }
  };

  const hasMissingRequired = request.questions.some((question) => {
    if (!question.required) return false;
    const value = answers[question.id];
    return Array.isArray(value) ? value.length === 0 : !String(value || '').trim();
  });
  const hasAnyAnswer = request.questions.some((question) => {
    const value = answers[question.id];
    return Array.isArray(value) ? value.length > 0 : Boolean(String(value || '').trim());
  });
  const hasRequiredQuestions = request.questions.some((question) => question.required);

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
              {question.required ? (
                <span className="ml-1 text-[var(--color-error)]">*</span>
              ) : (
                <span className="ml-1 font-normal text-[var(--color-text-tertiary)]">选填</span>
              )}
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
                    onClick={() => setAnswer(
                      question.id,
                      !question.required && answers[question.id] === option ? '' : option,
                    )}
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
        <Button
          type="primary"
          loading={submitting || submittingOverride}
          disabled={hasMissingRequired || cancelSubmittingOverride}
          onClick={handleSubmit}
        >
          {!hasAnyAnswer && !hasRequiredQuestions ? '跳过并继续' : '提交并继续'}
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
