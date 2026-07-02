import type {
  ChatRunPlanState,
  ClarificationRequestInfo,
  DisplaySnapshot,
  HitlRequestInfo,
  SkillCallInfo,
  ToolCallInfo,
} from '../../hooks/useEventStream';
import { MessageImages, StreamingPlaceholder } from './MessageAtoms';
import type { LocalMessage } from './types';
import { ChatDisplayRenderer } from './ChatDisplayRenderer';
import { MessageFrame, TokenUsage } from './MessageFrame';
import { buildLiveChatDisplayMessage, buildStoredChatDisplayMessage } from './chatDisplayProjector';

type ResolveHitl = (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;

type TeamSelectionProps = {
  selectedTeamActorId: string | null;
  onSelectTeamActor: (id: string) => void;
};

type ClarificationSubmitProps = {
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
};

export function MessageBubble({
  message,
  selectedTeamActorId,
  onSelectTeamActor,
  onSubmitClarification,
}: {
  message: LocalMessage;
} & TeamSelectionProps & ClarificationSubmitProps) {
  if (message.role === 'user') {
    return <UserMessageBubble message={message} />;
  }

  return (
      <SingleAgentMessageBubble
        message={message}
        selectedTeamActorId={selectedTeamActorId}
        onSelectTeamActor={onSelectTeamActor}
        onSubmitClarification={onSubmitClarification}
      />
  );
}

function UserMessageBubble({ message }: { message: LocalMessage }) {
  return (
    <MessageFrame isUser containIntrinsicSize="88px">
      <MessageImages images={message.images} align="right" />
      <div className="rounded-xl bg-[var(--color-bg-secondary)] px-3 py-2 text-sm leading-7 text-[var(--color-text-primary)]">
        {message.content}
      </div>
    </MessageFrame>
  );
}

function SingleAgentMessageBubble({
  message,
  selectedTeamActorId,
  onSelectTeamActor,
  onSubmitClarification,
}: {
  message: LocalMessage;
} & TeamSelectionProps & ClarificationSubmitProps) {
  const displayMessage = buildStoredChatDisplayMessage(message);

  return (
    <MessageFrame containIntrinsicSize="220px">
      <MessageImages images={message.images} align="left" />

      <ChatDisplayRenderer
        message={displayMessage}
        defaultProcessExpanded={false}
        toolbarCreatedAt={message.createdAt || new Date().toISOString()}
        showToolbar={Boolean(displayMessage.finalAnswer) && !displayMessage.blocks.some((block) => block.type === 'human_feedback')}
        onSubmitClarification={onSubmitClarification}
        selectedTeamActorId={selectedTeamActorId}
        onSelectTeamActor={onSelectTeamActor}
      />

      <TokenUsage message={message} />
    </MessageFrame>
  );
}

export function StreamingBubble({
  isStreaming,
  currentRunId,
  textContent,
  reasoningContent,
  finalAnswerContent,
  thinkingContent,
  displaySnapshot,
  skillCalls,
  hitlRequests,
  clarificationRequests,
  plans,
  onSubmitClarification,
  onResolveHitl,
  selectedTeamActorId,
  onSelectTeamActor,
  toolCalls,
  modelName,
  tokenUsage,
  error,
  runStartedAt,
  runCompletedAt,
}: {
  isStreaming: boolean;
  currentRunId: string;
  textContent: string;
  reasoningContent: string;
  finalAnswerContent: string;
  thinkingContent: string;
  displaySnapshot: DisplaySnapshot;
  skillCalls: SkillCallInfo[];
  hitlRequests: HitlRequestInfo[];
  clarificationRequests: ClarificationRequestInfo[];
  plans: ChatRunPlanState[];
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
  onResolveHitl: ResolveHitl;
  selectedTeamActorId: string | null;
  onSelectTeamActor: (id: string) => void;
  toolCalls: ToolCallInfo[];
  modelName: string;
  tokenUsage: { input: number; output: number } | null;
  error: string | null;
  runStartedAt: string;
  runCompletedAt: string;
}) {
  const displayMessage = buildLiveChatDisplayMessage({
    isStreaming,
    currentRunId,
    runStartedAt,
    runCompletedAt,
    textContent,
    reasoningContent,
    finalAnswerContent,
    thinkingContent,
    toolCalls,
    skillCalls,
    teamActors: [],
    plans,
    displaySnapshot,
    hitlRequests,
    clarificationRequests,
    modelName,
    tokenUsage,
    error,
  });

  return (
    <MessageFrame containIntrinsicSize="220px">
      <ChatDisplayRenderer
        message={displayMessage}
        defaultProcessExpanded={!textContent && !finalAnswerContent}
        toolbarCreatedAt={new Date().toISOString()}
        showToolbar={Boolean(displayMessage.finalAnswer)}
        onSubmitClarification={onSubmitClarification}
        onResolveHitl={onResolveHitl}
        selectedTeamActorId={selectedTeamActorId}
        onSelectTeamActor={onSelectTeamActor}
      />

      {displayMessage.blocks.length === 0 && (
        <StreamingPlaceholder modelName={modelName} />
      )}
    </MessageFrame>
  );
}
