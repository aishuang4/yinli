import { AlertCircle } from 'lucide-react';
import { MessageContentBlock } from './MessageAtoms';
import { MessageToolbar } from './MessageToolbar';
import { GeneratedFilesBlock } from './ToolArtifacts';
import { HumanFeedbackDisplayBlock } from './HumanFeedbackDisplayBlock';
import { ProcessDisplayBlock } from './ProcessDisplayBlock';
import { TeamDisplayBlock } from './TeamDisplayBlock';
import type { ChatDisplayMessage } from './chatDisplayTypes';

type ResolveHitl = (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;

export function ChatDisplayRenderer({
  message,
  defaultProcessExpanded,
  toolbarCreatedAt,
  showToolbar,
  onSubmitClarification,
  onResolveHitl,
  selectedTeamActorId,
  onSelectTeamActor,
}: {
  message: ChatDisplayMessage;
  defaultProcessExpanded: boolean;
  toolbarCreatedAt: string;
  showToolbar: boolean;
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
  onResolveHitl?: ResolveHitl;
  selectedTeamActorId?: string | null;
  onSelectTeamActor?: (id: string) => void;
}) {
  const hasFinalAnswer = message.blocks.some((block) => (
    block.type === 'final_answer' && Boolean(block.content.trim())
  ));

  return (
    <>
      {message.blocks.map((block) => {
        if (block.type === 'team') {
          return (
            <TeamDisplayBlock
              key={block.id}
              actors={block.actors}
              selectedActorId={selectedTeamActorId || null}
              onSelectActor={onSelectTeamActor || (() => {})}
            />
          );
        }

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
            <MessageContentBlock
              key={block.id}
              content={block.content}
              role="assistant"
              streaming={message.isStreaming}
            />
          );
        }

        if (block.type === 'generated_files') {
          return <GeneratedFilesBlock key={block.id} files={block.files} />;
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
