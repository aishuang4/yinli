/** 消息区滚到底部按钮。Chat 与群聊工作室共用。 */

import { ChevronDown } from 'lucide-react';

interface ScrollToBottomButtonProps {
  visible: boolean;
  onClick: () => void;
}

export function ScrollToBottomButton({ visible, onClick }: ScrollToBottomButtonProps) {
  return (
    <button
      type="button"
      className={`icon-button absolute bottom-4 left-1/2 h-9 w-9 -translate-x-1/2 rounded-full border border-[var(--color-border)] bg-[var(--color-bg-primary)]/70 p-0 text-[var(--color-text-secondary)] shadow-sm backdrop-blur transition-all duration-300 ease-out hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)] ${
        visible
          ? 'pointer-events-auto opacity-100'
          : 'pointer-events-none translate-y-2 opacity-0'
      }`}
      onClick={onClick}
      aria-label="滚动到底部"
      aria-hidden={!visible}
    >
      <ChevronDown size={16} />
    </button>
  );
}
