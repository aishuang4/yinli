import { useEffect, useRef, useState } from 'react';
import { Accordion } from '@agentscope-ai/chat';
import { formatProcessDuration } from './chatUiUtils';
import { AccordionProcessTimeline } from './ProcessTimeline';
import type { ChatProcessBlock } from './chatDisplayTypes';

export function ProcessDisplayBlock({
  block,
  defaultExpanded,
  hasFinalAnswer,
}: {
  block: ChatProcessBlock;
  defaultExpanded: boolean;
  hasFinalAnswer: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded && !hasFinalAnswer);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const hadFinalAnswerRef = useRef(hasFinalAnswer);
  const processDuration = formatProcessDuration(block.startedAt, block.completedAt, nowMs);
  const detailCount = block.items.length;
  const hasProcessDetails = detailCount > 0;
  const isTerminalStatus = block.status === 'completed' || block.status === 'failed' || block.status === 'cancelled';
  const displayStatus = hasFinalAnswer && !isTerminalStatus ? 'completed' : block.status;
  const isProcessDone = Boolean(block.completedAt) || hasFinalAnswer || isTerminalStatus;
  const processTitle = isProcessDone
    ? '已处理'
    : <Accordion.SoftLightTitle>处理中</Accordion.SoftLightTitle>;
  const processMeta = (
    <span className="flex shrink-0 items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
      {processDuration && <span>耗时 {processDuration}</span>}
      <span>{detailCount} 项</span>
    </span>
  );

  useEffect(() => {
    if (hasProcessDetails && !hasFinalAnswer) {
      setExpanded(true);
      hadFinalAnswerRef.current = hasFinalAnswer;
      return;
    }
    if (!hadFinalAnswerRef.current && hasFinalAnswer) {
      setExpanded(false);
    }
    hadFinalAnswerRef.current = hasFinalAnswer;
  }, [hasFinalAnswer, hasProcessDetails]);

  useEffect(() => {
    if (isProcessDone || !block.startedAt) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [block.startedAt, isProcessDone]);

  if (!hasProcessDetails) return null;

  return (
    <AccordionProcessTimeline
      accordionKey={`${block.id}-${hasFinalAnswer ? 'answer' : 'process'}`}
      items={block.items}
      autoScroll={!isProcessDone && (block.status === 'running' || block.status === 'pending')}
      title={processTitle}
      status={displayStatus}
      rightChildren={processMeta}
      defaultOpen={!hasFinalAnswer && expanded}
    />
  );
}
