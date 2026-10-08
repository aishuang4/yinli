/**
 * 群聊消息区：空态、暂停提示、审批条和消息列表。
 */

import type { RefObject, WheelEvent } from 'react';
import { Users } from 'lucide-react';
import { Spinner } from '@/components/design';
import { ScrollToBottomButton } from '../../components/ScrollToBottomButton';
import { GroupApprovalBar } from './GroupApprovalBar';
import { StudioMessageList } from './StudioMessageList';
import type { GroupStudioApproval, GroupStudioMember, GroupStudioSnapshot } from './types';
import type { StudioTimelineMessage } from './useGroupStudioStream';

interface StudioTranscriptProps {
  error: string;
  runtimeError: string;
  studio: GroupStudioSnapshot | null;
  groupId: string | null;
  timeline: StudioTimelineMessage[];
  members: GroupStudioMember[];
  pendingApproval: GroupStudioApproval | null;
  approvalMemberName: string;
  approvalBusy: boolean;
  showScrollToBottom: boolean;
  messagesContainerRef: RefObject<HTMLDivElement | null>;
  messagesEndRef: RefObject<HTMLDivElement | null>;
  onScroll: () => void;
  onWheel: (event: WheelEvent<HTMLDivElement>) => void;
  onPointerDown: () => void;
  onScrollToBottom: () => void;
  onDecide: (decision: 'approve' | 'deny') => void;
}

export function StudioTranscript({
  error,
  runtimeError,
  studio,
  groupId,
  timeline,
  members,
  pendingApproval,
  approvalMemberName,
  approvalBusy,
  showScrollToBottom,
  messagesContainerRef,
  messagesEndRef,
  onScroll,
  onWheel,
  onPointerDown,
  onScrollToBottom,
  onDecide,
}: StudioTranscriptProps) {
  return (
    <div className="relative min-h-0 overflow-hidden">
      <div
        ref={messagesContainerRef}
        className="h-full min-h-0 overflow-auto px-8 py-4"
        onScroll={onScroll}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        role="log"
        aria-live="polite"
        aria-label="群聊消息"
      >
        <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-4">
          {error ? (
            <div className="group-studio-runtime-error">{error}</div>
          ) : !studio && groupId ? (
            <div className="chat-empty-state flex flex-1 flex-col items-center justify-center gap-3 text-center text-[var(--color-text-tertiary)]">
              <Spinner />
              <div>正在打开工作室…</div>
            </div>
          ) : (
            <>
              {runtimeError ? <div className="group-studio-runtime-error">{runtimeError}</div> : null}
              {studio?.pauseReason === 'circuit' && studio.status === 'paused' ? (
                <div className="group-studio-approval" role="status">
                  <div className="group-studio-approval__title">
                    先停一下：这一轮说得有点多。再发一条就可以接着聊。
                  </div>
                </div>
              ) : null}
              {pendingApproval ? (
                <GroupApprovalBar
                  approval={pendingApproval}
                  memberName={approvalMemberName}
                  submitting={approvalBusy}
                  onDecide={onDecide}
                />
              ) : null}
              {timeline.length === 0 ? (
                <div className="chat-empty-state flex flex-1 flex-col items-center justify-center gap-3 text-center text-[var(--color-text-tertiary)]">
                  <div className="chat-empty-state__icon">
                    <Users size={42} strokeWidth={1.2} />
                  </div>
                  <div className="text-lg font-medium">开始群聊</div>
                  <div className="max-w-[420px] text-sm">
                    先在右侧把人拉进来，再发一句。用 @ 菜单点名；不点名就是全员各说一轮。
                  </div>
                </div>
              ) : (
                <>
                  <StudioMessageList members={members} messages={timeline} />
                  <div ref={messagesEndRef} />
                </>
              )}
            </>
          )}
        </div>
      </div>
      <ScrollToBottomButton
        visible={showScrollToBottom}
        onClick={onScrollToBottom}
      />
    </div>
  );
}
