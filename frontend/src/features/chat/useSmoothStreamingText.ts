import { useEffect, useRef, useState } from 'react';

const FRAME_MS = 32;

type SmoothStreamingTextOptions = {
  animateOnMount?: boolean;
  animateStreamingOnMount?: boolean;
  charsPerSecond?: number;
  maxCharsPerFrame?: number;
};

export type SmoothStreamingTextState = {
  content: string;
  isAnimating: boolean;
};

export function useSmoothStreamingText(
  content: string,
  streaming: boolean,
  options: SmoothStreamingTextOptions = {},
): SmoothStreamingTextState {
  const {
    animateOnMount = false,
    animateStreamingOnMount = true,
    charsPerSecond = 84,
    maxCharsPerFrame = 14,
  } = options;
  const shouldAnimateInitialContent = (
    (streaming && animateStreamingOnMount) ||
    (animateOnMount && content.length > 0)
  );
  const [displayedContent, setDisplayedContent] = useState(() => (shouldAnimateInitialContent ? '' : content));
  const [isAnimating, setIsAnimating] = useState(() => shouldAnimateInitialContent);
  const displayedRef = useRef(shouldAnimateInitialContent ? '' : content);
  const targetRef = useRef(content);
  const carryRef = useRef(0);

  useEffect(() => {
    targetRef.current = content;

    if (!content.startsWith(displayedRef.current)) {
      displayedRef.current = streaming ? '' : content;
      carryRef.current = 0;
      setDisplayedContent(displayedRef.current);
      setIsAnimating(streaming);
      return;
    }

    setIsAnimating(streaming || displayedRef.current !== content);
  }, [content, streaming]);

  useEffect(() => {
    if (!isAnimating) return;

    const timer = window.setInterval(() => {
      const current = displayedRef.current;
      const target = targetRef.current;
      if (current === target) {
        carryRef.current = 0;
        if (!streaming) setIsAnimating(false);
        return;
      }

      const remaining = target.length - current.length;
      if (remaining <= 0 || !target.startsWith(current)) {
        displayedRef.current = target;
        carryRef.current = 0;
        setDisplayedContent(target);
        if (!streaming) setIsAnimating(false);
        return;
      }

      carryRef.current += (charsPerSecond * FRAME_MS) / 1000;
      const catchUpBoost = remaining > 1200 ? 4 : remaining > 480 ? 2 : 1;
      const nextCount = Math.max(
        1,
        Math.min(maxCharsPerFrame * catchUpBoost, Math.floor(carryRef.current)),
      );
      carryRef.current = Math.max(0, carryRef.current - nextCount);

      const next = target.slice(0, current.length + Math.min(remaining, nextCount));
      displayedRef.current = next;
      setDisplayedContent(next);
      if (next === target && !streaming) {
        carryRef.current = 0;
        setIsAnimating(false);
      }
    }, FRAME_MS);

    return () => window.clearInterval(timer);
  }, [charsPerSecond, isAnimating, maxCharsPerFrame, streaming]);

  return {
    content: displayedContent,
    isAnimating: streaming || isAnimating,
  };
}
