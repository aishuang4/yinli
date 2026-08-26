/** 工作室快捷键，只挂 ChatPage。Chat keep-alive 覆盖去程和回程。 */

import { useEffect, type RefObject } from 'react';

interface UseGroupStudioHotkeyOptions {
  enabled: boolean;
  isComposingRef: RefObject<boolean>;
  onToggle: () => void;
}

export function useGroupStudioHotkey({
  enabled,
  isComposingRef,
  onToggle,
}: UseGroupStudioHotkeyOptions): void {
  useEffect(() => {
    if (!enabled) {
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.repeat) {
        return;
      }
      const modifier = event.metaKey || event.ctrlKey;
      if (!modifier || !event.shiftKey || event.key.toLowerCase() !== 'g') {
        return;
      }
      if (event.isComposing || isComposingRef.current) {
        return;
      }
      event.preventDefault();
      onToggle();
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [enabled, isComposingRef, onToggle]);
}
