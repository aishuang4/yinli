import { useEffect, useRef, useState } from 'react';

const FRAME_MS = 32;

type SmoothStreamingTextOptions = {
  charsPerSecond?: number;
  maxCharsPerFrame?: number;
};

export function useSmoothStreamingText(
  content: string,
  streaming: boolean,
  options: SmoothStreamingTextOptions = {},
): string {
  const { charsPerSecond = 84, maxCharsPerFrame = 14 } = options;
  const [displayedContent, setDisplayedContent] = useState(() => (streaming ? '' : content));
  const displayedRef = useRef(streaming ? '' : content);
  const targetRef = useRef(content);
  const carryRef = useRef(0);

  useEffect(() => {
    targetRef.current = content;

    if (!streaming) {
      displayedRef.current = content;
      carryRef.current = 0;
      setDisplayedContent(content);
      return;
    }

    if (!content.startsWith(displayedRef.current)) {
      displayedRef.current = '';
      carryRef.current = 0;
      setDisplayedContent('');
    }
  }, [content, streaming]);

  useEffect(() => {
    if (!streaming) return;

    const timer = window.setInterval(() => {
      const current = displayedRef.current;
      const target = targetRef.current;
      if (current === target) return;

      const remaining = target.length - current.length;
      if (remaining <= 0 || !target.startsWith(current)) {
        displayedRef.current = target;
        setDisplayedContent(target);
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
    }, FRAME_MS);

    return () => window.clearInterval(timer);
  }, [charsPerSecond, maxCharsPerFrame, streaming]);

  return displayedContent;
}
