import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { Bot, BookOpenCheck, ChevronDown, ChevronRight } from 'lucide-react';
import { Accordion, type IAccordionProps } from '@agentscope-ai/chat';
import { Tag } from '@agentscope-ai/design';
import {
  SparkBookLine,
  SparkCheckCircleLine,
  SparkDeepThinkLine,
  SparkErrorCircleLine,
  SparkLoadingLine,
  SparkTimeLine,
  SparkToolLine,
} from '@agentscope-ai/icons';
import { MarkdownContent } from '../../components/MarkdownContent';
import type { SkillCallInfo } from '../../hooks/useEventStream';
import type { ChatDisplayStatus, ChatProcessItem } from './chatDisplayTypes';
import { isAskUserToolName } from './humanInteractionUtils';
import { useSmoothStreamingText } from './useSmoothStreamingText';
import type { SubagentTimelineItem } from '../../types/subagent';

export function AccordionProcessTimeline({
  items,
  autoScroll = false,
  title,
  status,
  rightChildren,
  defaultOpen,
  accordionKey,
  alwaysOpen = false,
}: {
  items: ChatProcessItem[];
  autoScroll?: boolean;
  title: string | ReactElement;
  status: ChatDisplayStatus;
  rightChildren?: ReactElement;
  defaultOpen: boolean;
  accordionKey: string;
  alwaysOpen?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const scrollSignature = useMemo(
    () => items.map((item) => `${item.id}:${item.status}:${item.updatedAt}:${processItemContentLength(item)}`).join('|'),
    [items],
  );
  const sequentialReveal = autoScroll && toAccordionStatus(status) === 'generating';
  const itemOrderSignature = useMemo(() => items.map((item) => item.id).join('|'), [items]);
  const [revealedItemCount, setRevealedItemCount] = useState(
    sequentialReveal ? Math.min(1, items.length) : items.length,
  );

  useEffect(() => {
    if (!sequentialReveal) {
      setRevealedItemCount(items.length);
      return;
    }
    setRevealedItemCount((current) => {
      const nextCount = items.length === 0
        ? 0
        : Math.min(Math.max(current || 1, 1), items.length);
      return current === nextCount ? current : nextCount;
    });
  }, [itemOrderSignature, items.length, sequentialReveal]);

  const revealNextItem = useCallback(() => {
    setRevealedItemCount((current) => (current >= items.length ? current : current + 1));
  }, [items.length]);

  const handleTextAnimationChange = useCallback((itemId: string, isAnimating: boolean) => {
    if (!sequentialReveal || isAnimating) return;
    const itemIndex = items.findIndex((item) => item.id === itemId);
    if (itemIndex === revealedItemCount - 1) {
      revealNextItem();
    }
  }, [items, revealNextItem, revealedItemCount, sequentialReveal]);

  useEffect(() => {
    if (!sequentialReveal) return;
    const current = items[revealedItemCount - 1];
    if (!current || revealedItemCount >= items.length) return;
    if ((current.type === 'reasoning' || current.type === 'thinking') && current.content.trim()) return;
    const timer = window.setTimeout(revealNextItem, 160);
    return () => window.clearTimeout(timer);
  }, [items, revealNextItem, revealedItemCount, scrollSignature, sequentialReveal]);

  const visibleItems = sequentialReveal
    ? items.slice(0, revealedItemCount)
    : items;
  const visibleScrollSignature = sequentialReveal
    ? `${scrollSignature}:visible-${revealedItemCount}`
    : scrollSignature;
  const steps = useMemo(() => visibleItems
    .map((item) => processItemToAccordionStep(
      item,
      items.findIndex((source) => source.id === item.id) === items.length - 1,
      status,
      handleTextAnimationChange,
    ))
    .filter((step): step is IAccordionProps => Boolean(step)), [handleTextAnimationChange, items, status, visibleItems]);

  useLayoutEffect(() => {
    if (!autoScroll || !shouldStickToBottomRef.current) return;
    const node = scrollRef.current;
    if (!node) return;

    node.scrollTop = node.scrollHeight;
    const frame = window.requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [autoScroll, visibleScrollSignature]);

  const handleScroll = () => {
    const node = scrollRef.current;
    if (!node) return;
    const distanceToBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
    shouldStickToBottomRef.current = distanceToBottom < 48;
  };

  return (
    <Accordion
      key={accordionKey}
      title={title}
      status={toAccordionStatus(status)}
      defaultOpen={defaultOpen}
      open={alwaysOpen ? true : undefined}
      rightChildren={rightChildren}
    >
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="overflow-visible px-2 py-2 pr-1"
      >
        <div className="space-y-1">
          {steps.map((step) => (
            <Accordion key={step.id} {...step} />
          ))}
        </div>
      </div>
    </Accordion>
  );
}

function processItemContentLength(item: ChatProcessItem): number {
  if (item.type === 'reasoning' || item.type === 'thinking') {
    return item.content.length;
  }
  if (item.type === 'tool') {
    return `${item.toolCall.args}${item.toolCall.result}${item.toolCall.status}${item.toolCall.subagentTimeline?.length ?? 0}`.length;
  }
  if (item.type === 'skill') {
    return `${item.skillCall.content}${item.skillCall.status}`.length;
  }
  return 0;
}

function processItemToAccordionStep(
  item: ChatProcessItem,
  isLast: boolean,
  processStatus: ChatDisplayStatus,
  onTextAnimationChange?: (itemId: string, isAnimating: boolean) => void,
): IAccordionProps | null {
  if (item.type === 'reasoning') {
    return textStepFromProcessItem(item, 'Reasoning', <SparkTimeLine size={14} />, isLast, processStatus, onTextAnimationChange);
  }
  if (item.type === 'thinking') {
    return textStepFromProcessItem(item, 'Thinking', <SparkDeepThinkLine size={14} />, isLast, processStatus, onTextAnimationChange);
  }
  if (item.type === 'tool') {
    const toolStatus = item.toolCall.status || item.status;
    const isAskUser = isAskUserToolName(item.toolCall.name);
    const subagent = getSubagentDelegationInfo(item.toolCall);
    if (isPlanExitTool(item.toolCall.name)) {
      return {
        id: `${item.type}-${item.id}`,
        title: 'Plan · 完成计划',
        status: 'finished',
        icon: <SparkCheckCircleLine size={14} />,
        iconLine: !isLast,
        rightChildren: <StepStatusText status="completed" />,
      };
    }
    return {
      id: `${item.type}-${item.id}`,
      title: titleForStatus(
        subagent
          ? `Subagent · ${subagent.name}`
          : isAskUser
            ? '人工反馈 · 模型反问'
            : `Action · ${item.toolCall.name}`,
        toolStatus,
      ),
      status: toAccordionStatus(toolStatus),
      icon: subagent ? subagentIconForStatus(toolStatus) : toolIconForStatus(toolStatus),
      iconLine: !isLast,
      defaultOpen: item.toolCall.status === 'calling' || item.toolCall.status === 'running' || item.toolCall.status === 'failed',
      rightChildren: <StepStatusText status={toolStatus} />,
      children: (
        <ToolInputOutputPanels
          input={item.toolCall.args}
          output={item.toolCall.result}
          isAskUser={isAskUser}
          subagent={subagent}
        />
      ),
    };
  }
  if (item.type === 'skill') {
    const skillStatus = item.skillCall.status || item.status;
    return {
      id: `${item.type}-${item.id}`,
      title: titleForStatus(`Skill · ${item.skillCall.name}`, skillStatus),
      status: toAccordionStatus(skillStatus),
      icon: iconForStatus(skillStatus, <SparkBookLine size={14} />),
      iconLine: !isLast,
      defaultOpen: item.skillCall.status === 'running' || item.skillCall.status === 'failed',
      rightChildren: <StepStatusText status={skillStatus} />,
      children: (
        <Accordion.BodyContent>
          <div className="p-2">
            <SkillCallBlock skillCall={item.skillCall} />
          </div>
        </Accordion.BodyContent>
      ),
    };
  }
  return null;
}

function isPlanExitTool(name: string): boolean {
  return String(name || '').trim().toLowerCase() === 'plan_exit';
}

function textStepFromProcessItem(
  item: Extract<ChatProcessItem, { type: 'reasoning' | 'thinking' }>,
  title: string,
  fallbackIcon: ReactElement,
  isLast: boolean,
  processStatus: ChatDisplayStatus,
  onTextAnimationChange?: (itemId: string, isAnimating: boolean) => void,
): IAccordionProps | null {
  if (!item.content.trim()) return null;
  const displayStatus = textItemDisplayStatus(item.status, processStatus, isLast);
  const isGenerating = toAccordionStatus(displayStatus) === 'generating';
  return {
    id: `${item.type}-${item.id}`,
    title: isGenerating ? <Accordion.SoftLightTitle>{title}</Accordion.SoftLightTitle> : title,
    status: toAccordionStatus(displayStatus),
    icon: iconForStatus(displayStatus, fallbackIcon),
    iconLine: !isLast,
    defaultOpen: true,
    open: item.type === 'reasoning' ? true : undefined,
    rightChildren: <StepStatusText status={displayStatus} />,
    children: (
      <Accordion.BodyContent>
        <div className="p-3 text-sm leading-7 text-[var(--color-text-primary)]">
          <StreamingProcessMarkdown
            key={item.id}
            content={item.content}
            streaming={isGenerating}
            onAnimationChange={(isAnimating) => onTextAnimationChange?.(item.id, isAnimating)}
          />
        </div>
      </Accordion.BodyContent>
    ),
  };
}

function StreamingProcessMarkdown({
  content,
  streaming,
  onAnimationChange,
}: {
  content: string;
  streaming: boolean;
  onAnimationChange?: (isAnimating: boolean) => void;
}) {
  const visible = useSmoothStreamingText(content, streaming, {
    charsPerSecond: 112,
    maxCharsPerFrame: 18,
  });
  useEffect(() => {
    onAnimationChange?.(visible.isAnimating);
  }, [onAnimationChange, visible.isAnimating]);

  return (
    <>
      <MarkdownContent content={visible.content} className="w-full" />
      {visible.isAnimating && <span className="chat-stream-caret chat-stream-caret--subtle" aria-hidden="true" />}
    </>
  );
}

function textItemDisplayStatus(
  itemStatus: ChatDisplayStatus | string | undefined,
  processStatus: ChatDisplayStatus | string | undefined,
  isLast: boolean,
): ChatDisplayStatus | string {
  if (processStatus === 'failed' || processStatus === 'cancelled') return processStatus;
  if (processStatus === 'completed') return 'completed';
  if (!isLast) return 'completed';
  return itemStatus || processStatus || 'running';
}

function toAccordionStatus(status: ChatDisplayStatus | string | undefined): IAccordionProps['status'] {
  if (status === 'completed') return 'finished';
  if (status === 'failed') return 'error';
  if (status === 'cancelled') return 'interrupted';
  if (status === 'running' || status === 'pending' || status === 'waiting' || status === 'calling') {
    return 'generating';
  }
  return undefined;
}

function iconForStatus(status: ChatDisplayStatus | string | undefined, fallbackIcon: ReactElement): ReactElement {
  const accordionStatus = toAccordionStatus(status);
  if (accordionStatus === 'generating') {
    return <SparkLoadingLine size={14} spin />;
  }
  if (accordionStatus === 'finished') {
    return <SparkCheckCircleLine size={14} />;
  }
  if (accordionStatus === 'error') {
    return <SparkErrorCircleLine size={14} className="text-[var(--color-error)]" />;
  }
  return fallbackIcon;
}

function toolIconForStatus(status: ChatDisplayStatus | string | undefined): ReactElement {
  const accordionStatus = toAccordionStatus(status);
  if (accordionStatus === 'generating') {
    return <SparkLoadingLine size={14} spin />;
  }
  if (accordionStatus === 'error') {
    return <SparkErrorCircleLine size={14} className="text-[var(--color-error)]" />;
  }
  return <SparkToolLine size={14} />;
}

function subagentIconForStatus(status: ChatDisplayStatus | string | undefined): ReactElement {
  const accordionStatus = toAccordionStatus(status);
  if (accordionStatus === 'generating') {
    return <SparkLoadingLine size={14} spin />;
  }
  if (accordionStatus === 'error') {
    return <SparkErrorCircleLine size={14} className="text-[var(--color-error)]" />;
  }
  return <Bot size={14} />;
}

function titleForStatus(title: string, status: ChatDisplayStatus | string | undefined): string | ReactElement {
  if (toAccordionStatus(status) !== 'error') return title;
  return (
    <span className="text-[var(--color-error)]">
      {title}
    </span>
  );
}

type SubagentDelegationInfo = {
  name: string;
  task: string;
  summary: string;
  error: string;
  status: string;
  timeline: SubagentTimelineItem[];
};

function getSubagentDelegationInfo(toolCall: {
  name: string;
  args: string;
  result: string;
  subagentName?: string;
  subagentStatus?: string;
  subagentTimeline?: SubagentTimelineItem[];
  subagentSummary?: string;
  subagentError?: string;
  status?: string;
}): SubagentDelegationInfo | null {
  const toolName = String(toolCall.name || '').trim();
  const input = String(toolCall.args || '');
  const payload = parseToolPayload(input);
  const subagentName = toolCall.subagentName || pickPayloadString(payload, ['subagentName', 'subagent_name', 'agentName', 'name']);
  const task = pickPayloadString(payload, ['task', 'prompt', 'instruction', 'message']) || input;
  const isDelegateTask = normalizeToolKey(toolName) === 'delegatetask';
  if (!isDelegateTask && !subagentName) return null;
  return {
    name: subagentName || 'Subagent',
    task: stripCodeFence(task),
    summary: normalizeSubagentSummary(toolCall.subagentSummary || toolCall.result),
    error: stripCodeFence(toolCall.subagentError || ''),
    status: toolCall.subagentStatus || toolCall.status || '',
    timeline: toolCall.subagentTimeline || [],
  };
}

function parseToolPayload(input: string): Record<string, unknown> {
  const trimmed = input.trim();
  if (!trimmed) return {};
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    const matches = [...trimmed.matchAll(/["']?(subagentName|subagent_name|agentName)["']?\s*[:=]\s*["']([^"',\n}]+)["']/gi)];
    return Object.fromEntries(matches.map((match) => [match[1], match[2]]));
  }
}

function pickPayloadString(payload: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function normalizeToolKey(name: string): string {
  return String(name || '').replace(/[\s_-]+/g, '').toLowerCase();
}

function normalizeSubagentSummary(output: string): string {
  return stripCodeFence(String(output || ''))
    .replace(/^Subagent\s+.+?\s+completed\.\s*\n+/i, '')
    .replace(/^Summary:\s*/i, '')
    .trim();
}

function stripCodeFence(value: string): string {
  const trimmed = String(value || '').trim();
  const match = /^```(?:json|markdown|md|text)?\s*\n([\s\S]*?)\n```$/i.exec(trimmed);
  return (match ? match[1] : trimmed).trim();
}

function StepStatusText({ status }: { status: ChatDisplayStatus | string | undefined }) {
  const accordionStatus = toAccordionStatus(status);
  const text = accordionStatus === 'generating'
    ? '进行中'
    : accordionStatus === 'error'
      ? '失败'
      : accordionStatus === 'interrupted'
        ? '已停止'
        : accordionStatus === 'finished'
          ? '完成'
          : '';
  if (!text) return null;
  return (
    <span className={`text-xs ${accordionStatus === 'error' ? 'text-[var(--color-error)]' : 'text-[var(--color-text-tertiary)]'}`}>
      {text}
    </span>
  );
}

function ToolInputOutputPanels({
  input,
  output,
  isAskUser = false,
  subagent = null,
}: {
  input: string;
  output: string;
  isAskUser?: boolean;
  subagent?: SubagentDelegationInfo | null;
}) {
  const hasInput = Boolean(input.trim());
  const hasOutput = Boolean(output.trim());
  if (subagent) {
    return <SubagentDelegationPanels subagent={subagent} />;
  }
  if (!hasInput && !hasOutput) {
    return (
      <Accordion.BodyContent>
        <div className="p-3 text-xs text-[var(--color-text-tertiary)]">
          暂无工具输入或输出
        </div>
      </Accordion.BodyContent>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {hasInput && (
        <Accordion.BodyContent headerLeft={isAskUser ? '模型反问' : 'Input'}>
          <ToolPreBlock value={input} />
        </Accordion.BodyContent>
      )}
      {hasOutput && (
        <Accordion.BodyContent headerLeft={isAskUser ? '用户补充信息' : 'Output'}>
          <ToolPreBlock value={output} />
        </Accordion.BodyContent>
      )}
    </div>
  );
}

function SubagentDelegationPanels({ subagent }: { subagent: SubagentDelegationInfo }) {
  const hasTask = Boolean(subagent.task.trim());
  const hasSummary = Boolean(subagent.summary.trim());
  const hasError = Boolean(subagent.error.trim());
  const hasTimeline = subagent.timeline.length > 0;
  if (!hasTask && !hasSummary && !hasTimeline && !hasError) {
    return (
      <Accordion.BodyContent>
        <div className="p-3 text-xs text-[var(--color-text-tertiary)]">
          Subagent 委托已创建，等待任务详情
        </div>
      </Accordion.BodyContent>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {hasTask && (
        <Accordion.BodyContent headerLeft="委托任务">
          <div className="rounded-md bg-[var(--color-bg-primary)] p-3 text-sm leading-7 text-[var(--color-text-primary)]">
            <MarkdownContent content={subagent.task} className="w-full" />
          </div>
        </Accordion.BodyContent>
      )}
      {(subagent.status === 'running' || subagent.status === 'calling' || subagent.status === 'waiting' || subagent.status === 'failed') && (
        <Accordion.BodyContent headerLeft="当前状态">
          <SubagentCurrentStatusBlock timeline={subagent.timeline} status={subagent.status} />
        </Accordion.BodyContent>
      )}
      {hasError && (
        <Accordion.BodyContent headerLeft="执行错误">
          <div className="rounded-md bg-[var(--color-bg-primary)] p-3 text-sm leading-7 text-[var(--color-error)]">
            <MarkdownContent content={subagent.error} className="w-full" />
          </div>
        </Accordion.BodyContent>
      )}
      {hasSummary && (
        <Accordion.BodyContent headerLeft="返回主 Agent 的结果">
          <div className="rounded-md bg-[var(--color-bg-primary)] p-3 text-sm leading-7 text-[var(--color-text-primary)]">
            <MarkdownContent content={subagent.summary} className="w-full" />
          </div>
        </Accordion.BodyContent>
      )}
    </div>
  );
}

function SubagentCurrentStatusBlock({
  timeline,
  status,
}: {
  timeline: SubagentTimelineItem[];
  status: string;
}) {
  const current = pickCurrentSubagentTimelineItem(timeline, status);
  const title = current?.title || defaultSubagentStatusTitle(status);
  const detail = current?.detail || '';
  return (
    <div className="rounded-md bg-[var(--color-bg-primary)] p-3">
      <div className="grid grid-cols-[16px_1fr] gap-2">
        {status === 'running' || status === 'calling'
          ? <SparkLoadingLine size={14} spin className="mt-1 text-[var(--color-primary)]" />
          : <span className={`mt-1.5 h-2 w-2 rounded-full ${subagentTimelineDotClass(status)}`} />}
        <div className="min-w-0">
          <div className="text-sm font-medium text-[var(--color-text-primary)]">
            {title}
          </div>
          {detail && (
            <div className="mt-1 line-clamp-3 text-xs leading-6 text-[var(--color-text-secondary)]">
              <MarkdownContent content={detail} className="w-full" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function pickCurrentSubagentTimelineItem(
  timeline: SubagentTimelineItem[],
  status: string,
): SubagentTimelineItem | null {
  if (timeline.length === 0) return null;
  if (status === 'completed') {
    return findLastSubagentTimelineItem(timeline, (item) => item.type === 'SUBAGENT_COMPLETED') || timeline[timeline.length - 1];
  }
  if (status === 'failed') {
    return findLastSubagentTimelineItem(timeline, (item) => item.status === 'failed') || timeline[timeline.length - 1];
  }
  return timeline[timeline.length - 1];
}

function findLastSubagentTimelineItem(
  timeline: SubagentTimelineItem[],
  predicate: (item: SubagentTimelineItem) => boolean,
): SubagentTimelineItem | null {
  for (let index = timeline.length - 1; index >= 0; index -= 1) {
    if (predicate(timeline[index])) return timeline[index];
  }
  return null;
}

function defaultSubagentStatusTitle(status: string): string {
  if (status === 'completed') return '已返回结果给主 Agent';
  if (status === 'failed') return 'Subagent 执行失败';
  if (status === 'waiting') return '等待人工确认';
  return 'Subagent 正在运行';
}

function subagentTimelineDotClass(status: string): string {
  if (status === 'failed') return 'bg-[var(--color-error)]';
  if (status === 'completed') return 'bg-[var(--color-success)]';
  if (status === 'waiting') return 'bg-[var(--color-warning)]';
  return 'bg-[var(--color-primary)]';
}

function ToolPreBlock({ value }: { value: string }) {
  const formatted = formatToolTextValue(value);
  return (
    <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words bg-[var(--color-bg-primary)] p-3 font-mono text-xs leading-5 text-[var(--color-text-secondary)]">
      {formatted}
    </pre>
  );
}

function formatToolTextValue(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

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
        type="button"
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
