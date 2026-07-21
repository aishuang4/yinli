import { ClarificationBlock, HitlApprovalBlock } from './HumanInteractionBlocks';
import type { ChatHumanFeedbackBlock } from './chatDisplayTypes';

type ResolveHitl = (
  interactionId: string,
  action: 'approve' | 'reject',
  externalResults?: Record<string, string>,
  rememberApproval?: boolean,
) => Promise<void>;

export function HumanFeedbackDisplayBlock({
  block,
  onSubmitClarification,
  onResolveHitl,
}: {
  block: ChatHumanFeedbackBlock;
  onSubmitClarification: (content: string, displayContent?: string) => Promise<void>;
  onResolveHitl?: ResolveHitl;
}) {
  return (
    <>
      {onResolveHitl && block.hitlRequests.map((request) => (
        <HitlApprovalBlock
          key={request.interactionId}
          request={request}
          onResolve={onResolveHitl}
        />
      ))}

      {block.clarificationRequests.map((request) => (
        <ClarificationBlock
          key={request.id}
          request={request}
          onSubmit={onSubmitClarification}
          readOnly={block.readOnlyClarification}
        />
      ))}
    </>
  );
}
