import { useCallback, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { Button } from '@/components/design';
import { copyTextToClipboard, formatMessageTime } from './chatUiUtils';

export function MessageToolbar({ markdown, createdAt }: { markdown: string; createdAt?: string }) {
  const [copied, setCopied] = useState(false);
  const messageTime = formatMessageTime(createdAt);

  const handleCopyMarkdown = useCallback(async () => {
    if (!markdown.trim()) return;
    await copyTextToClipboard(markdown);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }, [markdown]);

  return (
    <div className="flex items-center justify-between gap-2 px-1">
      <Button
        size="small"
        type="text"
        className="h-7 px-2 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)]"
        onClick={handleCopyMarkdown}
        disabled={!markdown.trim()}
        aria-label="复制文本"
      >
        {copied ? <Check size={13} /> : <Copy size={13} />}
        <span>{copied ? '已复制' : '复制文本'}</span>
      </Button>
      {messageTime && (
        <time
          dateTime={createdAt}
          className="shrink-0 text-xs text-[var(--color-text-tertiary)]"
        >
          {messageTime}
        </time>
      )}
    </div>
  );
}
