import type { ClarificationRequestInfo, HitlRequestInfo, TeamActivityInfo } from '../../hooks/useEventStream';
import type { LocalMessage } from './types';
import { ClarificationBlock, HitlApprovalBlock } from './HumanInteractionBlocks';
import { isAskUserHitlRequest } from './humanInteractionUtils';
import { MessageContentBlock, StreamingPlaceholder } from './MessageAtoms';
import { MessageToolbar } from './MessageToolbar';
import { TeamActivityPanel } from './TeamActivityViews';
import { GeneratedFilesBlock } from './ToolArtifacts';
import { collectGeneratedFiles } from './generatedFiles';
import { MessageFrame, TokenUsage } from './MessageFrame';
import {
  extractClarificationFromText,
  splitAssistantContentByRuntimeEvents,
  stripClarificationMarkup,
} from './messageContent';

type TeamSelectionProps = {
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
};

type ClarificationSubmitProps = {
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
};

type ResolveHitl = (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;

export function TeamMessageBubble({
  message,
  selectedTeamActivityId,
  onSelectTeamActivity,
  onSubmitClarification,
}: {
  message: LocalMessage;
} & TeamSelectionProps & ClarificationSubmitProps) {
  const clarification = extractClarificationFromText(message.content);
  const clarificationRequests = message.clarificationRequests || (clarification ? [clarification] : []);
  const contentParts = splitAssistantContentByRuntimeEvents(
    clarification ? stripClarificationMarkup(message.content) : message.content,
    message.runtimeEvents || [],
  );
  const generatedFiles = collectGeneratedFiles([], message.teamActivities || []);

  return (
    <MessageFrame containIntrinsicSize="260px">
      <TeamActivityPanel
        activities={message.teamActivities || []}
        selectedActivityId={selectedTeamActivityId}
        onSelectActivity={onSelectTeamActivity}
      />

      <ClarificationBlocks
        requests={clarificationRequests}
        onSubmit={onSubmitClarification}
        readOnly={message.clarificationResolved}
      />

      <MessageContentBlock content={contentParts.finalAnswer} role="assistant" />

      {generatedFiles.length > 0 && <GeneratedFilesBlock files={generatedFiles} />}

      {contentParts.finalAnswer && clarificationRequests.length === 0 && (
        <MessageToolbar markdown={contentParts.finalAnswer} createdAt={message.createdAt} />
      )}

      <TokenUsage message={message} />
    </MessageFrame>
  );
}

export function TeamStreamingBubble({
  finalAnswerContent,
  teamActivities,
  hitlRequests,
  clarificationRequests,
  onSubmitClarification,
  onResolveHitl,
  selectedTeamActivityId,
  onSelectTeamActivity,
  modelName,
}: {
  finalAnswerContent: string;
  teamActivities: TeamActivityInfo[];
  hitlRequests: HitlRequestInfo[];
  clarificationRequests: ClarificationRequestInfo[];
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
  onResolveHitl: ResolveHitl;
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  modelName: string;
}) {
  const finalAnswer = finalAnswerContent.trim();
  const generatedFiles = collectGeneratedFiles([], teamActivities);

  return (
    <MessageFrame containIntrinsicSize="260px">
      <TeamActivityPanel
        activities={teamActivities}
        selectedActivityId={selectedTeamActivityId}
        onSelectActivity={onSelectTeamActivity}
      />

      <HumanInteractionBlocks
        hitlRequests={hitlRequests}
        clarificationRequests={clarificationRequests}
        onSubmitClarification={onSubmitClarification}
        onResolveHitl={onResolveHitl}
      />

      <MessageContentBlock content={finalAnswer} role="assistant" streaming />

      {generatedFiles.length > 0 && <GeneratedFilesBlock files={generatedFiles} />}

      {finalAnswer && (
        <MessageToolbar markdown={finalAnswer} createdAt={new Date().toISOString()} />
      )}

      {!finalAnswer &&
        generatedFiles.length === 0 &&
        hitlRequests.length === 0 &&
        clarificationRequests.length === 0 &&
        teamActivities.length === 0 && (
          <StreamingPlaceholder modelName={modelName} />
        )}
    </MessageFrame>
  );
}

function ClarificationBlocks({
  requests,
  onSubmit,
  readOnly,
}: {
  requests: ClarificationRequestInfo[];
  onSubmit: (content: string, displayContent?: string) => Promise<void>;
  readOnly?: boolean;
}) {
  return (
    <>
      {requests.map((request) => (
        <ClarificationBlock
          key={request.id}
          request={request}
          onSubmit={onSubmit}
          readOnly={readOnly}
        />
      ))}
    </>
  );
}

function HumanInteractionBlocks({
  hitlRequests,
  clarificationRequests,
  onSubmitClarification,
  onResolveHitl,
}: {
  hitlRequests: HitlRequestInfo[];
  clarificationRequests: ClarificationRequestInfo[];
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
  onResolveHitl: ResolveHitl;
}) {
  return (
    <>
      {hitlRequests.filter(isAskUserHitlRequest).map((request) => (
        <HitlApprovalBlock
          key={request.interactionId}
          request={request}
          onResolve={onResolveHitl}
        />
      ))}

      <ClarificationBlocks
        requests={clarificationRequests}
        onSubmit={onSubmitClarification}
      />
    </>
  );
}
