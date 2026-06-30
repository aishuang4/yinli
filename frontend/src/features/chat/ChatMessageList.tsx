import { AlertCircle } from 'lucide-react';

import type {
  ClarificationRequestInfo,
  HitlRequestInfo,
  ModelCacheInfo,
  SkillCallInfo,
  TeamActivityInfo,
  ToolCallInfo,
} from '../../hooks/useEventStream';
import { RUNTIME_EVENT_TYPES, type ChatRuntimeEvent } from '../../types/chatEvents';
import type { LocalMessage } from './types';
import { ClarificationBlock, HitlApprovalBlock, isAskUserHitlRequest } from './HumanInteractionBlocks';
import { MessageContentBlock, MessageImages, StreamingPlaceholder } from './MessageAtoms';
import { MessageToolbar } from './MessageToolbar';
import { isPlanModeRuntimeEvent, ProcessDetailsBlock } from './ProcessDetailsBlock';
import { TeamActivityPanel } from './TeamActivityViews';
import { GeneratedFilesBlock, collectGeneratedFiles } from './ToolArtifacts';
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

export function MessageBubble({
  message,
  selectedTeamActivityId,
  onSelectTeamActivity,
  onSubmitClarification,
}: {
  message: LocalMessage;
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
}) {
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
      selectedTeamActivityId={selectedTeamActivityId}
      onSelectTeamActivity={onSelectTeamActivity}
      onSubmitClarification={onSubmitClarification}
    />
  );
}

function UserMessageBubble({ message }: { message: LocalMessage }) {
  return (
    <MessageFrame isUser containIntrinsicSize="88px">
      <MessageImages images={message.images} align="right" />
      <MessageContentBlock content={message.content} role="user" />
    </MessageFrame>
  );
}

function SingleAgentMessageBubble({
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
  const toolCalls = message.toolCalls || [];
  const runtimeEvents = message.runtimeEvents || [];
  const detailRuntimeEvents = runtimeEvents.filter((event) => !isPlanModeRuntimeEvent(event));
  const generatedFiles = collectGeneratedFiles(toolCalls, []);
  const hasProcessDetails = Boolean(
    contentParts.reactTrace ||
    message.thinking ||
    (message.skillCalls && message.skillCalls.length > 0) ||
    (message.modelCaches && message.modelCaches.length > 0) ||
    toolCalls.length > 0 ||
    detailRuntimeEvents.length > 0
  );

  return (
    <MessageFrame containIntrinsicSize="220px">
      <AssistantError error={message.error} />
      <MessageImages images={message.images} align="left" />

      {hasProcessDetails && (
        <ProcessDetailsBlock
          defaultExpanded={false}
          hasFinalAnswer={Boolean(contentParts.finalAnswer)}
          processStartedAt={message.processStartedAt}
          processCompletedAt={message.processCompletedAt}
          reactTraceContent={contentParts.reactTrace}
          thinkingContent={message.thinking || ''}
          teamActivities={[]}
          skillCalls={message.skillCalls || []}
          modelCaches={message.modelCaches || []}
          runtimeEvents={detailRuntimeEvents}
          toolCalls={toolCalls}
          selectedTeamActivityId={selectedTeamActivityId}
          onSelectTeamActivity={onSelectTeamActivity}
        />
      )}

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

function TeamMessageBubble({
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
      <AssistantError error={message.error} />
      <MessageImages images={message.images} align="left" />

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

export function StreamingBubble({
  textContent,
  reasoningContent,
  finalAnswerContent,
  thinkingContent,
  skillCalls,
  teamActivities,
  modelCaches,
  runtimeEvents,
  hitlRequests,
  clarificationRequests,
  onSubmitClarification,
  onResolveHitl,
  selectedTeamActivityId,
  onSelectTeamActivity,
  toolCalls,
  modelName,
  runStartedAt,
  runCompletedAt,
}: {
  textContent: string;
  reasoningContent: string;
  finalAnswerContent: string;
  thinkingContent: string;
  skillCalls: SkillCallInfo[];
  teamActivities: TeamActivityInfo[];
  modelCaches: ModelCacheInfo[];
  runtimeEvents: ChatRuntimeEvent[];
  hitlRequests: HitlRequestInfo[];
  clarificationRequests: ClarificationRequestInfo[];
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
  onResolveHitl: (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  toolCalls: ToolCallInfo[];
  modelName: string;
  runStartedAt: string;
  runCompletedAt: string;
}) {
  const sharedProps = {
    finalAnswerContent,
    hitlRequests,
    clarificationRequests,
    onSubmitClarification,
    onResolveHitl,
    modelName,
  };

  if (teamActivities.length > 0) {
    return (
      <TeamStreamingBubble
        {...sharedProps}
        teamActivities={teamActivities}
        selectedTeamActivityId={selectedTeamActivityId}
        onSelectTeamActivity={onSelectTeamActivity}
      />
    );
  }

  return (
    <SingleAgentStreamingBubble
      {...sharedProps}
      textContent={textContent}
      thinkingContent={thinkingContent}
      reasoningContent={reasoningContent}
      skillCalls={skillCalls}
      modelCaches={modelCaches}
      runtimeEvents={runtimeEvents}
      toolCalls={toolCalls}
      runStartedAt={runStartedAt}
      runCompletedAt={runCompletedAt}
      selectedTeamActivityId={selectedTeamActivityId}
      onSelectTeamActivity={onSelectTeamActivity}
    />
  );
}

function SingleAgentStreamingBubble({
  textContent,
  reasoningContent,
  finalAnswerContent,
  thinkingContent,
  skillCalls,
  modelCaches,
  runtimeEvents,
  hitlRequests,
  clarificationRequests,
  onSubmitClarification,
  onResolveHitl,
  selectedTeamActivityId,
  onSelectTeamActivity,
  toolCalls,
  modelName,
  runStartedAt,
  runCompletedAt,
}: {
  textContent: string;
  reasoningContent: string;
  finalAnswerContent: string;
  thinkingContent: string;
  skillCalls: SkillCallInfo[];
  modelCaches: ModelCacheInfo[];
  runtimeEvents: ChatRuntimeEvent[];
  hitlRequests: HitlRequestInfo[];
  clarificationRequests: ClarificationRequestInfo[];
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
  onResolveHitl: (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  toolCalls: ToolCallInfo[];
  modelName: string;
  runStartedAt: string;
  runCompletedAt: string;
}) {
  const finalAnswer = finalAnswerContent.trim();
  const reactTraceContent = reasoningContent.trim();
  const usesFinalAnswerFallback = hasMissingFinalAnswerFallback(runtimeEvents);
  const detailRuntimeEvents = runtimeEvents.filter((event) => !isPlanModeRuntimeEvent(event));
  const generatedFiles = collectGeneratedFiles(toolCalls, []);
  const hasProcessDetails = Boolean(
    reactTraceContent ||
    thinkingContent ||
    skillCalls.length > 0 ||
    modelCaches.length > 0 ||
    toolCalls.length > 0 ||
    detailRuntimeEvents.length > 0
  );

  return (
    <MessageFrame containIntrinsicSize="220px">
      {hasProcessDetails && (
        <ProcessDetailsBlock
          defaultExpanded={!textContent}
          hasFinalAnswer={Boolean(finalAnswer) && !usesFinalAnswerFallback}
          processStartedAt={runStartedAt}
          processCompletedAt={runCompletedAt}
          reactTraceContent={reactTraceContent}
          thinkingContent={thinkingContent}
          teamActivities={[]}
          skillCalls={skillCalls}
          modelCaches={modelCaches}
          runtimeEvents={detailRuntimeEvents}
          toolCalls={toolCalls}
          selectedTeamActivityId={selectedTeamActivityId}
          onSelectTeamActivity={onSelectTeamActivity}
        />
      )}

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
        toolCalls.length === 0 &&
        skillCalls.length === 0 &&
        modelCaches.length === 0 &&
        hitlRequests.length === 0 &&
        clarificationRequests.length === 0 &&
        !thinkingContent &&
        !reactTraceContent && (
          <StreamingPlaceholder modelName={modelName} />
        )}
    </MessageFrame>
  );
}

function hasMissingFinalAnswerFallback(runtimeEvents: ChatRuntimeEvent[]): boolean {
  return runtimeEvents.some((event) => (
    event.type === RUNTIME_EVENT_TYPES.AGENT_FINAL_ANSWER_DELTA &&
    event.reason === 'missing_final_answer_marker_fallback'
  ));
}

function TeamStreamingBubble({
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
  onResolveHitl: (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;
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

function MessageFrame({
  isUser = false,
  containIntrinsicSize,
  children,
}: {
  isUser?: boolean;
  containIntrinsicSize: string;
  children: React.ReactNode;
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

function AssistantError({ error }: { error?: string }) {
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
  onResolveHitl: (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;
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

function TokenUsage({ message }: { message: LocalMessage }) {
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

function isTeamAssistantMessage(message: LocalMessage): boolean {
  return message.role === 'assistant' && Boolean(message.teamActivities && message.teamActivities.length > 0);
}
