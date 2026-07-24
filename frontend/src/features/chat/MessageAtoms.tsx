import { useEffect } from 'react';
import { FileText, Loader2 } from 'lucide-react';

import { MarkdownContent } from '../../components/MarkdownContent';
import type { ChatFilePayload, ChatImagePayload } from './types';
import { useSmoothStreamingText } from './useSmoothStreamingText';

export function MessageImages({
  images,
  align = 'left',
}: {
  images?: ChatImagePayload[];
  align?: 'left' | 'right';
}) {
  if (!images || images.length === 0) return null;

  return (
    <div className={`flex flex-wrap gap-2 ${align === 'right' ? 'justify-end' : 'justify-start'}`}>
      {images.map((image, index) => (
        <img
          key={`${image.name}-${index}`}
          src={`data:${image.media_type};base64,${image.data}`}
          alt={image.name || `image-${index + 1}`}
          className="max-h-48 max-w-64 rounded-md border border-[var(--color-border)] object-contain"
        />
      ))}
    </div>
  );
}

export function MessageFiles({ files, align = 'left' }: { files?: ChatFilePayload[]; align?: 'left' | 'right' }) {
  if (!files || files.length === 0) return null;

  return (
    <div className={`flex flex-wrap gap-2 ${align === 'right' ? 'justify-end' : 'justify-start'}`}>
      {files.map((file) => (
        <div
          key={file.path}
          className="flex max-w-64 items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-2 py-1.5 text-xs text-[var(--color-text-secondary)]"
          title={file.path}
        >
          <FileText size={14} className="shrink-0" />
          <span className="truncate">{file.name}</span>
        </div>
      ))}
    </div>
  );
}

export function MessageContentBlock({
  content,
  role,
  streaming = false,
  animateOnMount = false,
  onAnimationChange,
  surface = 'default',
}: {
  content: string;
  role: 'user' | 'assistant';
  streaming?: boolean;
  animateOnMount?: boolean;
  onAnimationChange?: (isAnimating: boolean) => void;
  surface?: 'default' | 'transparent';
}) {
  const isUser = role === 'user';
  const visible = useSmoothStreamingText(content, streaming && !isUser, {
    animateOnMount: animateOnMount && !isUser,
    charsPerSecond: 76,
    maxCharsPerFrame: 12,
  });
  useEffect(() => {
    onAnimationChange?.(visible.isAnimating);
  }, [onAnimationChange, visible.isAnimating]);

  if (!content) return null;

  return (
    <div
      className={`chat-message-content ${isUser ? 'chat-message-content--user' : 'chat-message-content--assistant'}`}
      style={{
        padding: '10px 14px',
        borderRadius: 'var(--radius-xl)',
        backgroundColor: isUser
          ? 'var(--color-accent)'
          : surface === 'transparent'
            ? 'transparent'
            : 'var(--color-bg-secondary)',
        border: '0',
        boxShadow: 'none',
        outline: 'none',
        backgroundClip: 'padding-box',
        color: isUser
          ? 'var(--color-text-inverse)'
          : 'var(--color-text-primary)',
        fontSize: 'var(--text-base)',
        lineHeight: 'var(--leading-normal)',
        wordBreak: 'break-word',
        whiteSpace: isUser ? 'pre-wrap' : undefined,
      }}
    >
      {isUser ? content : <MarkdownContent content={visible.content} />}
      {visible.isAnimating && (
        <span className="chat-stream-caret" aria-hidden="true" />
      )}
    </div>
  );
}

export function StreamingPlaceholder({ modelName }: { modelName: string }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '10px 14px',
        color: 'var(--color-text-tertiary)',
        fontSize: 'var(--text-sm)',
      }}
    >
      <Loader2 size={14} className="animate-spin" />
      {modelName ? `${modelName} 正在处理...` : '正在连接 Agent...'}
    </div>
  );
}
