/** 驱动 Chat ↔ 工作室 520ms 三段过渡；只在 App 壳层注册定时器。 */

import { useEffect } from 'react';
import { useAppStore, type StudioTransition } from '../../stores/appStore';

const PHASE_MS: Record<Exclude<StudioTransition, 'idle'>, number> = {
  'enter-collapse': 180,
  'enter-expand': 220,
  'enter-settle': 120,
  'leave-collapse': 180,
  'leave-expand': 220,
  'leave-settle': 120,
};

export function useStudioTransition(): void {
  const studioTransition = useAppStore((state) => state.studioTransition);
  const advanceStudioTransition = useAppStore((state) => state.advanceStudioTransition);

  useEffect(() => {
    if (studioTransition === 'idle') {
      return;
    }
    const timeout = window.setTimeout(
      () => advanceStudioTransition(),
      PHASE_MS[studioTransition],
    );
    return () => window.clearTimeout(timeout);
  }, [advanceStudioTransition, studioTransition]);
}
