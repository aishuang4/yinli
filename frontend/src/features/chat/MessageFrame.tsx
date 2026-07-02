import type { ReactNode } from 'react';
import type { LocalMessage } from './types';

export function MessageFrame({
  isUser = false,
  containIntrinsicSize,
  children,
}: {
  isUser?: boolean;
  containIntrinsicSize: string;
  children: ReactNode;
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: isUser ? 'row-reverse' : 'row',
        alignItems: 'flex-start',
        contentVisibility: 'auto',
        containIntrinsicSize,
      }}
    >
      <div
        style={{
          width: isUser ? 'auto' : '100%',
          maxWidth: isUser ? '75%' : '100%',
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
        }}
      >
        {children}
      </div>
    </div>
  );
}

export function TokenUsage({ message }: { message: LocalMessage }) {
  if (!message.tokenUsage) return null;
  return (
    <div
      style={{
        fontSize: 'var(--text-xs)',
        color: 'var(--color-text-tertiary)',
        paddingLeft: '4px',
      }}
    >
      {message.modelName && `${message.modelName} · `}
      {message.tokenUsage.input} + {message.tokenUsage.output} tokens
    </div>
  );
}
