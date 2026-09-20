/** Chat 历史与实时消息统一使用 DisplaySnapshot V4 Block Tree。 */

import { AlertCircle } from 'lucide-react';
import { RuntimeBlockTree } from '../runtime-stream/RuntimeBlockTree';
import type { DisplaySnapshot } from '../runtime-stream/protocol';
import { hasVisibleRuntimeContent, runtimeText } from '../runtime-stream/selectors';
import { MessageContentBlock, MessageFiles, MessageImages, StreamingPlaceholder } from './MessageAtoms';
import { MessageFrame } from './MessageFrame';
import { MessageToolbar } from './MessageToolbar';
import type { FilePreviewTarget } from '../file-preview/filePreview';
import type { LocalMessage } from './types';

type ResolveInteraction = (
  interactionId: string,
  action: 'approve' | 'reject',
  externalResults?: Record<string, string>,
  rememberApproval?: boolean,
) => Promise<void>;

export function MessageBubble({
  message,
  sessionId,
  previewPath,
  onOpenPreview,
}: {
  message: LocalMessage;
  sessionId?: string;
  previewPath?: string;
  onOpenPreview?: (target: FilePreviewTarget) => void;
}) {
  if (message.role === 'user') {
    return (
      <MessageFrame isUser containIntrinsicSize="88px">
        <MessageImages images={message.images} align="right" />
        <MessageFiles files={message.files} align="right" />
        <MessageContentBlock content={message.content} role="user" />
      </MessageFrame>
    );
  }
  const markdown = message.displaySnapshot ? runtimeText(message.displaySnapshot) : message.content;
  if (message.displaySnapshot && !hasVisibleRuntimeContent(message.displaySnapshot) && !message.error) {
    return null;
  }
  return (
    <MessageFrame containIntrinsicSize="220px">
      <MessageImages images={message.images} align="left" />
      {message.displaySnapshot ? (
        <RuntimeBlockTree
          snapshot={message.displaySnapshot}
          sessionId={sessionId}
          processStopped={Boolean(message.cancelled || message.error)}
          actions={{ previewPath, onOpenPreview }}
        />
      ) : (
        <MessageContentBlock content={message.content} role="assistant" surface="transparent" />
      )}
      {message.error ? <ErrorNotice message={message.error} /> : null}
      {markdown.trim() ? <MessageToolbar markdown={markdown} createdAt={message.createdAt} /> : null}
    </MessageFrame>
  );
}

export function StreamingBubble({
  snapshot,
  isStreaming,
  cancelled = false,
  modelName,
  error,
  createdAt,
  sessionId,
  previewPath,
  onOpenPreview,
  onResolveInteraction,
  onAnswerAnimationChange,
}: {
  snapshot: DisplaySnapshot;
  isStreaming: boolean;
  cancelled?: boolean;
  modelName: string;
  error: string | null;
  createdAt?: string;
  sessionId?: string;
  previewPath?: string;
  onOpenPreview?: (target: FilePreviewTarget) => void;
  onResolveInteraction: ResolveInteraction;
  onAnswerAnimationChange?: (isAnimating: boolean) => void;
}) {
  const markdown = runtimeText(snapshot);
  const hasVisibleContent = hasVisibleRuntimeContent(snapshot);
  if (!hasVisibleContent && !isStreaming && !error) return null;
  return (
    <MessageFrame containIntrinsicSize="220px">
      {hasVisibleContent ? (
        <RuntimeBlockTree
          snapshot={snapshot}
          sessionId={sessionId}
          processStopped={!isStreaming && cancelled}
          actions={{ onResolveInteraction, previewPath, onOpenPreview }}
          onAnswerAnimationChange={onAnswerAnimationChange}
        />
      ) : isStreaming ? (
        <StreamingPlaceholder modelName={modelName} />
      ) : null}
      {error ? <ErrorNotice message={error} /> : null}
      {!isStreaming && snapshot.streamDone && markdown.trim() ? (
        <MessageToolbar markdown={markdown} createdAt={createdAt} />
      ) : null}
    </MessageFrame>
  );
}

function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 border-l-2 border-[var(--color-error)] px-3 py-2 text-sm text-[var(--color-error)]">
      <AlertCircle size={15} className="mt-0.5 shrink-0" />
      <span className="break-words">{message}</span>
    </div>
  );
}
