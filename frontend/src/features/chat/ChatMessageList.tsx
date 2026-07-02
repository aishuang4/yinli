import type {
  ChatRunPlanState,
  ClarificationRequestInfo,
  DisplaySnapshot,
  HitlRequestInfo,
  SkillCallInfo,
  TeamActivityInfo,
  ToolCallInfo,
} from '../../hooks/useEventStream';
import { MessageImages, StreamingPlaceholder } from './MessageAtoms';
import type { LocalMessage } from './types';
import { ChatDisplayRenderer } from './ChatDisplayRenderer';
import { MessageFrame, TokenUsage } from './MessageFrame';
import { TeamMessageBubble, TeamStreamingBubble } from './TeamMessageBubbles';
import { buildLiveChatDisplayMessage, buildStoredChatDisplayMessage } from './chatDisplayProjector';

type ResolveHitl = (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;

type TeamSelectionProps = {
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
};

type ClarificationSubmitProps = {
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
};

export function MessageBubble({
  message,
  selectedTeamActivityId,
  onSelectTeamActivity,
  onSubmitClarification,
}: {
  message: LocalMessage;
} & TeamSelectionProps & ClarificationSubmitProps) {
  if (message.role === 'user') {
    return <UserMessageBubble message={message} />;
  }

  if (isTeamAssistantMessage(message)) {
    return (
      <TeamMessageBubble
        message={message}
        selectedTeamActivityId={selectedTeamActivityId}
        onSelectTeamActivity={onSelectTeamActivity}
        onSubmitClarification={onSubmitClarification}
      />
    );
  }

  return (
      <SingleAgentMessageBubble
        message={message}
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
  onSubmitClarification,
}: {
  message: LocalMessage;
} & ClarificationSubmitProps) {
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
  teamActivities,
  hitlRequests,
  clarificationRequests,
  plans,
  onSubmitClarification,
  onResolveHitl,
  selectedTeamActivityId,
  onSelectTeamActivity,
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
  teamActivities: TeamActivityInfo[];
  hitlRequests: HitlRequestInfo[];
  clarificationRequests: ClarificationRequestInfo[];
  plans: ChatRunPlanState[];
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
  onResolveHitl: ResolveHitl;
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  toolCalls: ToolCallInfo[];
  modelName: string;
  tokenUsage: { input: number; output: number } | null;
  error: string | null;
  runStartedAt: string;
  runCompletedAt: string;
}) {
  if (teamActivities.length > 0) {
    return (
      <TeamStreamingBubble
        finalAnswerContent={finalAnswerContent}
        teamActivities={teamActivities}
        hitlRequests={hitlRequests}
        clarificationRequests={clarificationRequests}
        onSubmitClarification={onSubmitClarification}
        onResolveHitl={onResolveHitl}
        selectedTeamActivityId={selectedTeamActivityId}
        onSelectTeamActivity={onSelectTeamActivity}
        modelName={modelName}
      />
    );
  }

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
    teamActivities: [],
    runtimeEvents: [],
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
      />

      {displayMessage.blocks.length === 0 && (
        <StreamingPlaceholder modelName={modelName} />
      )}
    </MessageFrame>
  );
}

function isTeamAssistantMessage(message: LocalMessage): boolean {
  return message.role === 'assistant' && Boolean(message.teamActivities && message.teamActivities.length > 0);
}
