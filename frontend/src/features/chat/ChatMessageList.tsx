import { AlertCircle } from 'lucide-react';

import type { ClarificationRequestInfo, HitlRequestInfo, ModelCacheInfo, SkillCallInfo, TeamActivityInfo, ToolCallInfo } from '../../hooks/useEventStream';
import type { ChatRuntimeEvent } from '../../types/chatEvents';
import type { LocalMessage } from './types';
import { ClarificationBlock, HitlApprovalBlock, isAskUserHitlRequest } from './HumanInteractionBlocks';
import { MessageContentBlock, MessageImages, StreamingPlaceholder } from './MessageAtoms';
import { MessageToolbar } from './MessageToolbar';
import { ProcessDetailsBlock } from './ProcessDetailsBlock';
import { GeneratedFilesBlock, collectGeneratedFiles } from './ToolArtifacts';
import { extractClarificationFromText, splitAssistantContentByRuntimeEvents, stripClarificationMarkup } from './messageContent';

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
  const isUser = message.role === 'user';
  const clarification = !isUser ? extractClarificationFromText(message.content) : null;
  const clarificationRequests = !isUser
    ? message.clarificationRequests || (clarification ? [clarification] : [])
    : [];
  const contentParts = !isUser
    ? splitAssistantContentByRuntimeEvents(
      clarification ? stripClarificationMarkup(message.content) : message.content,
      message.runtimeEvents || [],
    )
    : { finalAnswer: message.content, reactTrace: '' };
  const generatedFiles = !isUser
    ? collectGeneratedFiles(message.toolCalls || [], message.teamActivities || [])
    : [];
  const hasProcessDetails = !isUser && Boolean(
    contentParts.reactTrace ||
    message.thinking ||
    (message.teamActivities && message.teamActivities.length > 0) ||
    (message.skillCalls && message.skillCalls.length > 0) ||
    (message.modelCaches && message.modelCaches.length > 0) ||
    (message.toolCalls && message.toolCalls.length > 0)
  );

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: isUser ? 'row-reverse' : 'row',
        alignItems: 'flex-start',
        contentVisibility: 'auto',
        containIntrinsicSize: isUser ? '88px' : '220px',
      }}
    >
      {/* 消息内容 */}
      <div
        style={{
          width: isUser ? 'auto' : '100%',
          maxWidth: isUser ? '75%' : '100%',
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
        }}
      >
        {/* 错误提示 */}
        {message.error && (
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
            {message.error}
          </div>
        )}

        <MessageImages images={message.images} align={isUser ? 'right' : 'left'} />

        {hasProcessDetails && (
          <ProcessDetailsBlock
            defaultExpanded={false}
            hasFinalAnswer={Boolean(contentParts.finalAnswer)}
            processStartedAt={message.processStartedAt}
            processCompletedAt={message.processCompletedAt}
            reactTraceContent={contentParts.reactTrace}
            thinkingContent={message.thinking || ''}
            teamActivities={message.teamActivities || []}
            skillCalls={message.skillCalls || []}
            modelCaches={message.modelCaches || []}
            runtimeEvents={message.runtimeEvents || []}
            toolCalls={message.toolCalls || []}
            selectedTeamActivityId={selectedTeamActivityId}
            onSelectTeamActivity={onSelectTeamActivity}
          />
        )}

        {/* 文本内容 */}
        {clarificationRequests.map((request) => (
          <ClarificationBlock
            key={request.id}
            request={request}
            onSubmit={onSubmitClarification}
            readOnly={message.clarificationResolved}
          />
        ))}

        <MessageContentBlock
          content={contentParts.finalAnswer}
          role={isUser ? 'user' : 'assistant'}
        />

        {!isUser && generatedFiles.length > 0 && (
          <GeneratedFilesBlock files={generatedFiles} />
        )}

        {!isUser && contentParts.finalAnswer && clarificationRequests.length === 0 && (
          <MessageToolbar markdown={contentParts.finalAnswer} createdAt={message.createdAt} />
        )}

        {/* Token 用量 */}
        {message.tokenUsage && (
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
        )}
      </div>
    </div>
  );
}

/**
 * StreamingBubble — 正在流式生成的回复气泡
 *
 * 实时显示 Agent 正在生成的内容，包括：
 * - 思考链（thinking）
 * - 工具调用（tool calls）
 * - 文本回复（text delta）
 */
export function StreamingBubble({
  textContent,
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
  const generatedFiles = collectGeneratedFiles(toolCalls, teamActivities);
  const contentParts = splitAssistantContentByRuntimeEvents(textContent, runtimeEvents, {
    streaming: true,
  });
  const hasProcessDetails = Boolean(
    contentParts.reactTrace ||
    thinkingContent ||
    teamActivities.length > 0 ||
    skillCalls.length > 0 ||
    modelCaches.length > 0 ||
    toolCalls.length > 0
  );

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
      }}
    >
      {/* 内容区域 */}
      <div
        style={{
          width: '100%',
          maxWidth: '100%',
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
        }}
      >
        {hasProcessDetails && (
          <ProcessDetailsBlock
            defaultExpanded={!textContent}
            hasFinalAnswer={Boolean(contentParts.finalAnswer)}
            processStartedAt={runStartedAt}
            processCompletedAt={runCompletedAt}
            reactTraceContent={contentParts.reactTrace}
            thinkingContent={thinkingContent}
            teamActivities={teamActivities}
            skillCalls={skillCalls}
            modelCaches={modelCaches}
            runtimeEvents={runtimeEvents}
            toolCalls={toolCalls}
            selectedTeamActivityId={selectedTeamActivityId}
            onSelectTeamActivity={onSelectTeamActivity}
          />
        )}

        {hitlRequests.filter(isAskUserHitlRequest).map((request) => (
          <HitlApprovalBlock
            key={request.interactionId}
            request={request}
            onResolve={onResolveHitl}
          />
        ))}

        {clarificationRequests.map((request) => (
          <ClarificationBlock
            key={request.id}
            request={request}
            onSubmit={onSubmitClarification}
          />
        ))}

        {/* 文本内容 */}
        <MessageContentBlock
          content={contentParts.finalAnswer}
          role="assistant"
          streaming
        />

        {generatedFiles.length > 0 && (
          <GeneratedFilesBlock files={generatedFiles} />
        )}

        {contentParts.finalAnswer && (
          <MessageToolbar markdown={contentParts.finalAnswer} createdAt={new Date().toISOString()} />
        )}

        {/* 无内容时显示加载指示器 */}
        {!contentParts.finalAnswer && generatedFiles.length === 0 && toolCalls.length === 0 && skillCalls.length === 0 && teamActivities.length === 0 && modelCaches.length === 0 && hitlRequests.length === 0 && clarificationRequests.length === 0 && !thinkingContent && !contentParts.reactTrace && (
          <StreamingPlaceholder modelName={modelName} />
        )}
      </div>
    </div>
  );
}
