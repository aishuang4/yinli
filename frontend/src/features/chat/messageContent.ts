import type { ClarificationRequestInfo } from '../../hooks/useEventStream';
import { RUNTIME_EVENT_TYPES, type ChatRuntimeEvent } from '../../types/chatEvents';

export interface AssistantContentParts {
  finalAnswer: string;
  reactTrace: string;
}

export function splitAssistantContent(content: string): AssistantContentParts {
  const text = content.trim();
  if (!text) return { finalAnswer: '', reactTrace: '' };

  const processSentences = [
    '现在让我',
    '让我先',
    '我先',
    '我继续',
    '继续查看',
    '现在查看',
    '现在进入',
    '下面开始',
  ];
  const countProcessHits = (value: string) => (
    processSentences.reduce((count, marker) => count + (value.split(marker).length - 1), 0)
  );
  const headingMatches = [...text.matchAll(/^#{1,3}\s+.*(?:报告|总结|结论|建议|方案|结果|最终|交付|完成).*$\n?/gim)];
  const lateHeading = headingMatches.find((match) => {
    const index = match.index || 0;
    if (index <= 0) return false;
    const prefix = text.slice(0, index).trim();
    return prefix.length > 280 && countProcessHits(prefix) >= 2;
  });
  const startIndex = lateHeading?.index ?? -1;
  if (startIndex > 0) {
    return {
      reactTrace: text.slice(0, startIndex).trim(),
      finalAnswer: text.slice(startIndex).trim(),
    };
  }

  const finalMarker = text.search(/(?:^|\n)(?:---\s*\n+)?(?:##\s*)?(?:✅\s*)?(?:最终|结论|总结|交付|建议|改进建议)/m);
  if (finalMarker > 0) {
    const prefix = text.slice(0, finalMarker).trim();
    if (prefix.length <= 280 || countProcessHits(prefix) < 2) {
      return { finalAnswer: text, reactTrace: '' };
    }
    return {
      reactTrace: prefix,
      finalAnswer: text.slice(finalMarker).trim(),
    };
  }

  const processHitCount = countProcessHits(text);
  if (processHitCount >= 4 && text.length > 500) {
    const midpoint = Math.floor(text.length * 0.65);
    const nextParagraph = text.indexOf('\n\n', midpoint);
    if (nextParagraph > 0) {
      return {
        reactTrace: text.slice(0, nextParagraph).trim(),
        finalAnswer: text.slice(nextParagraph).trim(),
      };
    }
  }

  return { finalAnswer: text, reactTrace: '' };
}

export function splitAssistantContentByRuntimeEvents(
  content: string,
  runtimeEvents: ChatRuntimeEvent[] = [],
  options: { streaming?: boolean } = {},
): AssistantContentParts {
  if (runtimeEvents.length === 0) {
    if (options.streaming) {
      return {
        reactTrace: content.trim(),
        finalAnswer: '',
      };
    }
    return splitAssistantContent(content);
  }

  let outputBuffer = '';
  const traceParts: string[] = [];
  let sawActionAfterOutput = false;
  for (const event of runtimeEvents) {
    const isTeamProcessScope = event.scope === 'team' || event.scope === 'worker';
    if (event.type === RUNTIME_EVENT_TYPES.AGENT_REASONING_DELTA) {
      if (isTeamProcessScope) continue;
      traceParts.push(String(event.payload.delta || '').trim());
      continue;
    }
    if (event.type === RUNTIME_EVENT_TYPES.AGENT_FINAL_ANSWER_DELTA) {
      outputBuffer += String(event.payload.delta || '');
      sawActionAfterOutput = true;
      continue;
    }
    if (event.type === RUNTIME_EVENT_TYPES.AGENT_OUTPUT_DELTA) {
      // Worker/Team 普通过程输出只属于右侧协作详情，不属于 assistant 最终答案。
      // 单 Agent 旧链路可能仍使用 AGENT_OUTPUT_DELTA，因此只兼容 single scope。
      if (event.scope === 'single') {
        outputBuffer += String(event.payload.delta || '');
      }
      continue;
    }
    if (isRuntimeActionBoundary(event)) {
      if (outputBuffer.trim()) {
        traceParts.push(outputBuffer.trim());
        outputBuffer = '';
        sawActionAfterOutput = true;
      }
    }
  }

  if (!sawActionAfterOutput) {
    if (options.streaming && content.trim()) {
      return {
        reactTrace: mergeTextParts(traceParts),
        finalAnswer: content.trim(),
      };
    }
    return {
      reactTrace: content.trim(),
      finalAnswer: '',
    };
  }

  const finalAnswer = outputBuffer.trim();
  return {
    reactTrace: mergeTextParts(traceParts),
    finalAnswer,
  };
}

function mergeTextParts(parts: string[]): string {
  return parts
    .map((part) => part.trim())
    .filter(Boolean)
    .join('\n\n')
    .trim();
}

function isRuntimeActionBoundary(event: ChatRuntimeEvent): boolean {
  return (
    event.type === RUNTIME_EVENT_TYPES.TOOL_CALL_STARTED ||
    event.type === RUNTIME_EVENT_TYPES.TOOL_RESULT_STARTED ||
    event.type === RUNTIME_EVENT_TYPES.TOOL_RESULT_COMPLETED ||
    event.type === RUNTIME_EVENT_TYPES.SKILL_STARTED ||
    event.type === RUNTIME_EVENT_TYPES.SKILL_COMPLETED ||
    event.type === RUNTIME_EVENT_TYPES.ARTIFACT_CREATED ||
    event.type === RUNTIME_EVENT_TYPES.ARTIFACT_UPDATED ||
    event.type === RUNTIME_EVENT_TYPES.TEAM_WORKER_STARTED ||
    event.type === RUNTIME_EVENT_TYPES.TEAM_WORKER_COMPLETED
  );
}

export function extractClarificationFromText(content: string): ClarificationRequestInfo | null {
  const match = content.match(/<clarification>\s*([\s\S]*?)\s*<\/clarification>/);
  const rawText = match?.[1] ?? extractRawClarificationJson(content);
  if (!rawText) return null;

  try {
    const raw = JSON.parse(rawText);
    const questions = Array.isArray(raw.questions)
      ? raw.questions
        .map((question: Record<string, unknown>, index: number) => {
          const type = String(question.type || '');
          if (!['choice', 'multi_choice', 'text'].includes(type)) return null;
          const id = String(question.id || `q${index + 1}`);
          const label = String(question.label || question.question || '');
          if (!id || !label) return null;
          const options = Array.isArray(question.options)
            ? question.options.map((option) => String(option).trim()).filter(Boolean).slice(0, 5)
            : [];
          if ((type === 'choice' || type === 'multi_choice') && options.length < 2) return null;
          return {
            id,
            type: type as 'choice' | 'multi_choice' | 'text',
            label,
            options,
            placeholder: String(question.placeholder || ''),
            required: question.required !== false,
          };
        })
        .filter(Boolean)
      : [];
    if (questions.length === 0) return null;
    return {
      id: `clarification-${Date.now()}`,
      title: String(raw.title || '需要补充信息'),
      description: String(raw.description || ''),
      questions: questions as ClarificationRequestInfo['questions'],
      createdAt: new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

function extractRawClarificationJson(content: string): string | null {
  if (!content.includes('"title"') || !content.includes('"questions"')) return null;
  const start = content.indexOf('{');
  const end = content.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  return content.slice(start, end + 1);
}

export function stripClarificationMarkup(content: string): string {
  return content
    .replace(/<clarification>\s*[\s\S]*?\s*<\/clarification>/g, '')
    .trim();
}
