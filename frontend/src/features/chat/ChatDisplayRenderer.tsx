import { AlertCircle } from 'lucide-react';
import { MessageContentBlock } from './MessageAtoms';
import { MessageToolbar } from './MessageToolbar';
import { GeneratedFilesBlock } from './ToolArtifacts';
import { HumanFeedbackDisplayBlock } from './HumanFeedbackDisplayBlock';
import { ProcessDisplayBlock } from './ProcessDisplayBlock';
import type { ChatDisplayMessage } from './chatDisplayTypes';

type ResolveHitl = (
  interactionId: string,
  action: 'approve' | 'reject',
  externalResults?: Record<string, string>,
  rememberApproval?: boolean,
) => Promise<void>;

export function ChatDisplayRenderer({
  message,
  defaultProcessExpanded,
  toolbarCreatedAt,
  showToolbar,
  onSubmitClarification,
  onResolveHitl,
  animateStreamingText = false,
  showGeneratedFiles = true,
  onFinalAnswerAnimationChange,
}: {
  message: ChatDisplayMessage;
  defaultProcessExpanded: boolean;
  toolbarCreatedAt: string;
  showToolbar: boolean;
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
  onResolveHitl?: ResolveHitl;
  animateStreamingText?: boolean;
  showGeneratedFiles?: boolean;
  onFinalAnswerAnimationChange?: (isAnimating: boolean) => void;
}) {
  const hasFinalAnswer = message.blocks.some((block) => (
    block.type === 'final_answer' && Boolean(block.content.trim())
  ));

  return (
    <>
      {message.blocks.map((block) => {
        if (block.type === 'process') {
          return (
            <ProcessDisplayBlock
              key={block.id}
              block={block}
              defaultExpanded={defaultProcessExpanded}
              hasFinalAnswer={message.isStreaming ? false : hasFinalAnswer}
            />
          );
        }

        if (block.type === 'plan') return null;

        if (block.type === 'human_feedback') {
          return (
            <HumanFeedbackDisplayBlock
              key={block.id}
              block={block}
              onSubmitClarification={onSubmitClarification}
              onResolveHitl={onResolveHitl}
            />
          );
        }

        if (block.type === 'final_answer') {
          return (
            <div key={block.id} className="chat-final-answer-section">
              <div className="chat-final-answer-separator" aria-hidden="true">
                <span>最终回答</span>
              </div>
              <MessageContentBlock
                content={block.content}
                role="assistant"
                streaming={message.isStreaming}
                animateOnMount={animateStreamingText}
                onAnimationChange={onFinalAnswerAnimationChange}
                surface="transparent"
              />
            </div>
          );
        }

        if (block.type === 'generated_files') {
          return showGeneratedFiles
            ? <GeneratedFilesBlock key={block.id} files={block.files} sessionId={message.sessionId} />
            : null;
        }

        if (block.type === 'error') {
          return <AssistantError key={block.id} error={block.message} />;
        }

        return null;
      })}

      {showToolbar && message.finalAnswer && (
        <MessageToolbar markdown={message.finalAnswer} createdAt={toolbarCreatedAt} />
      )}
    </>
  );
}

export function AssistantError({ error }: { error?: string }) {
  if (!error) return null;
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        padding: '8px 12px',
        borderRadius: 'var(--radius-lg)',
        backgroundColor: 'var(--color-error-bg)',
        color: 'var(--color-error)',
        fontSize: 'var(--text-sm)',
      }}
    >
      <AlertCircle size={14} />
      {error}
    </div>
  );
}
