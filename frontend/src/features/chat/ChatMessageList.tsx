/** Chat 历史与实时消息统一使用 DisplaySnapshot V4 Block Tree。 */

import { AlertCircle } from 'lucide-react';
import { elapsedSeconds } from '../runtime-stream/processTrace';
import { RuntimeBlockTree } from '../runtime-stream/RuntimeBlockTree';
import { abandonOpenBlocks, type DisplaySnapshot } from '../runtime-stream/protocol';
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
  const historySnapshot = message.displaySnapshot ? abandonOpenBlocks(message.displaySnapshot) : undefined;
  const markdown = historySnapshot ? runtimeText(historySnapshot) : message.content;
  if (historySnapshot && !hasVisibleRuntimeContent(historySnapshot) && !message.error) {
    return null;
  }
  return (
    <MessageFrame containIntrinsicSize="220px">
      <MessageImages images={message.images} align="left" />
      {historySnapshot ? (
        <RuntimeBlockTree
          snapshot={historySnapshot}
          sessionId={sessionId}
          processStopped={Boolean(message.cancelled || message.error)}
          elapsedSeconds={elapsedSeconds(message.processStartedAt, message.processCompletedAt) ?? message.elapsedSeconds ?? null}
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
  startedAt,
  elapsedSeconds: elapsedOverride,
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
  startedAt?: string;
  elapsedSeconds?: number | null;
  sessionId?: string;
  previewPath?: string;
  onOpenPreview?: (target: FilePreviewTarget) => void;
  onResolveInteraction: ResolveInteraction;
  onAnswerAnimationChange?: (isAnimating: boolean) => void;
}) {
  const live = isStreaming;
  const displaySnapshot = live ? snapshot : abandonOpenBlocks(snapshot);
  const markdown = runtimeText(displaySnapshot);
  const hasVisibleContent = hasVisibleRuntimeContent(displaySnapshot);
  if (!hasVisibleContent && !isStreaming && !error) return null;
  return (
    <MessageFrame containIntrinsicSize="220px">
      {hasVisibleContent ? (
        <RuntimeBlockTree
          snapshot={displaySnapshot}
          sessionId={sessionId}
          processStopped={!live && (cancelled || Boolean(error))}
          elapsedSeconds={elapsedOverride ?? elapsedSeconds(startedAt, createdAt)}
          actions={{ onResolveInteraction: live ? onResolveInteraction : undefined, previewPath, onOpenPreview }}
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
