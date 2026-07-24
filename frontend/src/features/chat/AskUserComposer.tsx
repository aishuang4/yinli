/** 用底部唯一输入区回答当前 Snapshot 中开放的 AskUser。 */

import { useState } from 'react';
import { MessageCircleQuestion } from 'lucide-react';
import type { PendingAskUserInteraction } from './humanInteractionUtils';
import { ClarificationBlock } from './HumanInteractionBlocks';

type ResolveInteraction = (
  interactionId: string,
  action: 'approve' | 'reject',
  externalResults?: Record<string, string>,
) => Promise<void>;

export function AskUserComposer({
  interaction,
  pendingCount,
  isResuming,
  onResolve,
}: {
  interaction: PendingAskUserInteraction;
  pendingCount: number;
  isResuming: boolean;
  onResolve: ResolveInteraction;
}) {
  const [action, setAction] = useState<'submit' | 'cancel' | null>(null);
  const { hitlRequest, clarificationRequest, toolCallId } = interaction;

  return (
    <div className="px-8 pb-6 pt-2">
      <div className="mx-auto max-w-4xl">
        <div className="mb-2 flex items-center gap-2 px-1 text-xs text-[var(--color-text-tertiary)]">
          <MessageCircleQuestion size={14} />
          <span className="min-w-0 flex-1 truncate">
            {hitlRequest.agentName} 正在等待你的回答
          </span>
          {pendingCount > 1 ? <span>1 / {pendingCount} 个待回答</span> : null}
        </div>
        <ClarificationBlock
          key={hitlRequest.interactionId}
          request={clarificationRequest}
          onSubmit={async (content) => {
            setAction('submit');
            try {
              await onResolve(hitlRequest.interactionId, 'approve', {
                [toolCallId]: content,
              });
            } finally {
              setAction(null);
            }
          }}
          onCancel={async () => {
            setAction('cancel');
            try {
              await onResolve(hitlRequest.interactionId, 'reject');
            } finally {
              setAction(null);
            }
          }}
          submittingOverride={isResuming || action === 'submit'}
          cancelSubmittingOverride={action === 'cancel'}
        />
      </div>
    </div>
  );
}
