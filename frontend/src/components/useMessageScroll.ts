/** 消息区自动滚动、用户打断和回到底部控制。Chat 与群聊工作室共用。 */

import { useCallback, useEffect, useRef, useState, type WheelEvent } from 'react';

interface UseMessageScrollOptions {
  contentVersion: unknown;
  isStreaming: boolean;
}

export function useMessageScroll({ contentVersion, isStreaming }: UseMessageScrollOptions) {
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const programmaticScrollRef = useRef(false);
  const programmaticScrollTimerRef = useRef<number | null>(null);

  const cancelProgrammaticScroll = useCallback(() => {
    programmaticScrollRef.current = false;
    if (programmaticScrollTimerRef.current !== null) {
      window.clearTimeout(programmaticScrollTimerRef.current);
      programmaticScrollTimerRef.current = null;
    }
  }, []);

  const handleMessagesScroll = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    const nearBottom = distanceToBottom < 80;
    if (programmaticScrollRef.current) {
      shouldAutoScrollRef.current = true;
      setShowScrollToBottom(false);
      if (nearBottom) programmaticScrollRef.current = false;
      return;
    }
    shouldAutoScrollRef.current = nearBottom;
    setShowScrollToBottom(!nearBottom);
  }, []);

  const handleMessagesWheel = useCallback((event: WheelEvent<HTMLDivElement>) => {
    if (event.deltaY < 0) cancelProgrammaticScroll();
  }, [cancelProgrammaticScroll]);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    shouldAutoScrollRef.current = true;
    programmaticScrollRef.current = true;
    setShowScrollToBottom(false);
    const container = messagesContainerRef.current;
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior });
    } else {
      messagesEndRef.current?.scrollIntoView({ behavior });
    }
    if (programmaticScrollTimerRef.current !== null) {
      window.clearTimeout(programmaticScrollTimerRef.current);
    }
    programmaticScrollTimerRef.current = window.setTimeout(() => {
      programmaticScrollRef.current = false;
      programmaticScrollTimerRef.current = null;
    }, behavior === 'smooth' ? 700 : 80);
  }, []);

  const resetScroll = useCallback(() => {
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
  }, []);

  useEffect(() => {
    if (!shouldAutoScrollRef.current) return;
    scrollToBottom(isStreaming ? 'auto' : 'smooth');
  }, [contentVersion, isStreaming, scrollToBottom]);

  useEffect(() => {
    const container = messagesContainerRef.current;
    const content = container?.firstElementChild;
    if (!container || !content || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (shouldAutoScrollRef.current) scrollToBottom('auto');
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [scrollToBottom]);

  useEffect(() => () => {
    if (programmaticScrollTimerRef.current !== null) {
      window.clearTimeout(programmaticScrollTimerRef.current);
    }
  }, []);

  return {
    cancelProgrammaticScroll,
    handleMessagesScroll,
    handleMessagesWheel,
    messagesContainerRef,
    messagesEndRef,
    resetScroll,
    scrollToBottom,
    showScrollToBottom,
  };
}
