/** 群聊输入器，视觉对齐 ChatComposer。 */

import type { KeyboardEventHandler, ReactNode } from 'react';
import { Send } from 'lucide-react';

interface StudioComposerProps {
  disabled: boolean;
  sending: boolean;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
  onKeyDown: KeyboardEventHandler<HTMLTextAreaElement>;
  onSend: () => void;
  controls?: ReactNode;
}

export function StudioComposer({
  disabled,
  sending,
  value,
  placeholder,
  onChange,
  onKeyDown,
  onSend,
  controls,
}: StudioComposerProps) {
  return (
    <div className="px-8 pb-6 pt-2">
      <div className="chat-composer mx-auto max-w-4xl rounded-xl bg-[var(--color-bg-secondary)]">
        <textarea
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          rows={2}
          className="block max-h-[180px] w-full resize-none rounded-t-lg border-0 bg-transparent px-4 pb-0 pt-3 text-base text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:bg-[var(--color-bg-tertiary)]"
          aria-label="群聊消息输入框"
        />
        <div className="flex items-center justify-between gap-2 px-3 py-2">
          <div className="flex min-w-0 items-center gap-2">{controls}</div>
          <button
            type="button"
            className="icon-button flex h-8 w-8 items-center justify-center rounded-[10px] border border-[var(--color-border)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] disabled:cursor-not-allowed disabled:opacity-40"
            disabled={disabled || sending || !value.trim()}
            onClick={onSend}
            aria-label="发送消息"
          >
            <Send size={15} />
          </button>
        </div>
      </div>
    </div>
  );
}
