import { useLayoutEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { BookOpenCheck, ChevronDown, ChevronRight } from 'lucide-react';
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

export function AccordionProcessTimeline({
  items,
  autoScroll = false,
  title,
  status,
  rightChildren,
  defaultOpen,
  accordionKey,
}: {
  items: ChatProcessItem[];
  autoScroll?: boolean;
  title: string | ReactElement;
  status: ChatDisplayStatus;
  rightChildren?: ReactElement;
  defaultOpen: boolean;
  accordionKey: string;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const scrollSignature = useMemo(
    () => items.map((item) => `${item.id}:${item.status}:${item.updatedAt}:${processItemContentLength(item)}`).join('|'),
    [items],
  );
  const steps = useMemo(() => items
    .map((item, index) => processItemToAccordionStep(item, index === items.length - 1, status))
    .filter((step): step is IAccordionProps => Boolean(step)), [items, status]);

  useLayoutEffect(() => {
    if (!autoScroll || !shouldStickToBottomRef.current) return;
    const node = scrollRef.current;
    if (!node) return;

    node.scrollTop = node.scrollHeight;
    const frame = window.requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [autoScroll, scrollSignature]);

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
      rightChildren={rightChildren}
    >
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="max-h-[400px] overflow-y-auto px-2 py-2 pr-1"
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
    return `${item.toolCall.args}${item.toolCall.result}${item.toolCall.status}`.length;
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
): IAccordionProps | null {
  if (item.type === 'reasoning') {
    return textStepFromProcessItem(item, 'Reasoning', <SparkTimeLine size={14} />, isLast, processStatus);
  }
  if (item.type === 'thinking') {
    return textStepFromProcessItem(item, 'Thinking', <SparkDeepThinkLine size={14} />, isLast, processStatus);
  }
  if (item.type === 'tool') {
    const toolStatus = item.toolCall.status || item.status;
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
      title: titleForStatus(`Action · ${item.toolCall.name}`, toolStatus),
      status: toAccordionStatus(toolStatus),
      icon: toolIconForStatus(toolStatus),
      iconLine: !isLast,
      defaultOpen: item.toolCall.status === 'calling' || item.toolCall.status === 'running' || item.toolCall.status === 'failed',
      rightChildren: <StepStatusText status={toolStatus} />,
      children: (
        <ToolInputOutputPanels input={item.toolCall.args} output={item.toolCall.result} />
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
    rightChildren: <StepStatusText status={displayStatus} />,
    children: (
      <Accordion.BodyContent>
        <div className="p-3 text-sm leading-7 text-[var(--color-text-primary)]">
          <MarkdownContent content={item.content} className="w-full" />
        </div>
      </Accordion.BodyContent>
    ),
  };
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

function titleForStatus(title: string, status: ChatDisplayStatus | string | undefined): string | ReactElement {
  if (toAccordionStatus(status) !== 'error') return title;
  return (
    <span className="text-[var(--color-error)]">
      {title}
    </span>
  );
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

function ToolInputOutputPanels({ input, output }: { input: string; output: string }) {
  const hasInput = Boolean(input.trim());
  const hasOutput = Boolean(output.trim());
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
        <Accordion.BodyContent headerLeft="Input">
          <ToolPreBlock value={input} />
        </Accordion.BodyContent>
      )}
      {hasOutput && (
        <Accordion.BodyContent headerLeft="Output">
          <ToolPreBlock value={output} />
        </Accordion.BodyContent>
      )}
    </div>
  );
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
