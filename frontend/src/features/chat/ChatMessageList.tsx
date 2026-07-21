import type {
  ChatRunPlanState,
  ClarificationRequestInfo,
  DisplaySnapshot,
  HitlRequestInfo,
  SkillCallInfo,
  ToolCallInfo,
} from '../../hooks/useEventStream';
import { Loader2, Users } from 'lucide-react';
import { MessageFiles, MessageImages, StreamingPlaceholder } from './MessageAtoms';
import type { LocalMessage } from './types';
import type { TeamConsoleState } from '../../types/teamRuntime';
import { ChatDisplayRenderer } from './ChatDisplayRenderer';
import { MessageFrame } from './MessageFrame';
import { buildLiveChatDisplayMessage, buildStoredChatDisplayMessage } from './chatDisplayProjector';

type ResolveHitl = (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;

type ClarificationSubmitProps = {
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
};

export function MessageBubble({
  message,
  sessionId = '',
  onSubmitClarification,
}: {
  message: LocalMessage;
  sessionId?: string;
} & ClarificationSubmitProps) {
  if (message.role === 'user') {
    return <UserMessageBubble message={message} />;
  }

  return (
      <SingleAgentMessageBubble
        message={message}
        sessionId={sessionId}
        onSubmitClarification={onSubmitClarification}
      />
  );
}

function UserMessageBubble({ message }: { message: LocalMessage }) {
  return (
    <MessageFrame isUser containIntrinsicSize="88px">
      <MessageImages images={message.images} align="right" />
      <MessageFiles files={message.files} align="right" />
      <div className="rounded-xl bg-[var(--color-bg-secondary)] px-3 py-2 text-sm leading-7 text-[var(--color-text-primary)]">
        {message.content}
      </div>
    </MessageFrame>
  );
}

function SingleAgentMessageBubble({
  message,
  sessionId,
  onSubmitClarification,
}: {
  message: LocalMessage;
  sessionId: string;
} & ClarificationSubmitProps) {
  const displayMessage = buildStoredChatDisplayMessage(message, sessionId);

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
  teamConsole,
  skillCalls,
  hitlRequests,
  clarificationRequests,
  plans,
  onSubmitClarification,
  onResolveHitl,
  onFinalAnswerAnimationChange,
  showGeneratedFiles,
  sessionId,
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
  teamConsole: TeamConsoleState;
  skillCalls: SkillCallInfo[];
  hitlRequests: HitlRequestInfo[];
  clarificationRequests: ClarificationRequestInfo[];
  plans: ChatRunPlanState[];
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
  onResolveHitl: ResolveHitl;
  onFinalAnswerAnimationChange?: (isAnimating: boolean) => void;
  showGeneratedFiles: boolean;
  sessionId: string;
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
    teamConsole,
    plans,
    displaySnapshot,
    hitlRequests,
    clarificationRequests,
    modelName,
    tokenUsage,
    error,
  }, sessionId);

  return (
    <MessageFrame containIntrinsicSize="220px">
      <ChatDisplayRenderer
        message={displayMessage}
        defaultProcessExpanded={!textContent && !finalAnswerContent}
        toolbarCreatedAt={new Date().toISOString()}
        showToolbar={Boolean(displayMessage.finalAnswer)}
        onSubmitClarification={onSubmitClarification}
        onResolveHitl={onResolveHitl}
        animateStreamingText
        showGeneratedFiles={showGeneratedFiles}
        onFinalAnswerAnimationChange={onFinalAnswerAnimationChange}
      />

      {displayMessage.blocks.length === 0 && (
        teamConsole.snapshot
          ? <TeamStreamingPlaceholder state={teamConsole} />
          : <StreamingPlaceholder modelName={modelName} />
      )}
    </MessageFrame>
  );
}

function TeamStreamingPlaceholder({ state }: { state: TeamConsoleState }) {
  const snapshot = state.snapshot;
  if (!snapshot) return null;
  const phase = ({
    created: '正在创建 TeamRun',
    coordinating: 'Leader 正在组建团队',
    executing: '成员正在执行共享任务',
    synthesizing: 'Leader 正在综合协作结果',
    completed: 'TeamRun 已完成',
    failed: 'TeamRun 执行失败',
    cancelled: 'TeamRun 已取消',
  } as Record<string, string>)[snapshot.run.phase] || 'Team 正在协作';
  const active = snapshot.run.status === 'running';

  return (
    <div className="flex min-h-20 items-center gap-3 border-l-2 border-[var(--color-accent)] px-4 py-3">
      {active
        ? <Loader2 size={16} className="shrink-0 animate-spin text-[var(--color-accent)]" />
        : <Users size={16} className="shrink-0 text-[var(--color-accent)]" />}
      <div className="min-w-0">
        <div className="text-sm font-medium text-[var(--color-text-primary)]">{phase}</div>
        <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
          {snapshot.counts.succeeded}/{snapshot.counts.tasks} 个任务已完成 · {snapshot.counts.members} 名成员
        </div>
      </div>
    </div>
  );
}
