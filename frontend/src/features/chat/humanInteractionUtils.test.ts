import { describe, expect, it } from 'vitest';
import { modelDenyReview, toHitlRequest } from './humanInteractionUtils';
import type { DisplayBlock } from '../runtime-stream/protocol';

const review = {
  reviewable: true,
  reason: '模型拒绝了这一次：审核模型拒绝这一次',
  riskRule: '外发请求必须经过审核',
  riskClass: 'dangerous',
  reasonCode: 'human_review_of_model_deny',
  status: 'pending',
};

function approvalBlock(phase: 'open' | 'closed'): DisplayBlock<Record<string, unknown>> {
  return {
    id: 'approval:1',
    kind: 'approval',
    phase,
    data: {
      interactionId: 'hitl-1',
      type: 'REQUIRE_USER_CONFIRM',
      sessionId: 's',
      agentId: 'a',
      agentName: 'Agent',
      runId: 'r',
      scope: 'single',
      replyId: 'reply',
      toolCalls: [{ id: 'call-1', name: 'HttpRequest', input: '{"url":"https://example.com"}' }],
      decisions: [review],
      createdAt: '2026-10-20T00:00:00Z',
    },
  } as DisplayBlock<Record<string, unknown>>;
}

describe('model deny review card', () => {
  it('刷新后从同一块恢复拒绝原因和风险规则', () => {
    const request = toHitlRequest(approvalBlock('open'));
    expect(request).not.toBeNull();
    const card = modelDenyReview(request!);
    expect(card?.reason).toBe(review.reason);
    expect(card?.riskRule).toBe(review.riskRule);
    expect(card?.status).toBe('pending');
  });

  it('已结束的历史仍能读到复核事实，但状态不再是待批', () => {
    const closed = toHitlRequest(approvalBlock('closed'));
    const card = modelDenyReview({
      ...closed!,
      decisions: [{ ...review, status: 'rejected' }],
    });
    expect(card?.reviewable).toBe(true);
    expect(card?.status).toBe('rejected');
  });

  it('普通审批不是模型拒绝复核', () => {
    const request = toHitlRequest(approvalBlock('open'));
    expect(modelDenyReview({ ...request!, decisions: [{ reviewable: false, reason: '请批准' }] })).toBeNull();
  });
});
