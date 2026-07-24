/** 从唯一 DisplaySnapshot 投影中派生当前 Plan 审核状态。 */

import type { DisplaySnapshot } from '../runtime-stream/protocol';

export type PlanReviewData = {
  planId: string;
  title: string;
  goal: string;
  status: string;
  revision: number;
  markdown: string;
  steps: Array<Record<string, unknown>>;
};

function readPlan(snapshot?: DisplaySnapshot): PlanReviewData[] {
  if (!snapshot) return [];
  return snapshot.order.flatMap((id) => {
    const block = snapshot.blocksById[id];
    if (block?.kind !== 'plan' || !block.data || typeof block.data !== 'object' || Array.isArray(block.data)) {
      return [];
    }
    const data = block.data as Record<string, unknown>;
    const planId = String(data.planId || '');
    if (!planId) return [];
    return [{
      planId,
      title: String(data.title || '执行计划'),
      goal: String(data.goal || ''),
      status: String(data.status || ''),
      revision: Number(data.revision || 0),
      markdown: String(data.markdown || ''),
      steps: Array.isArray(data.steps)
        ? data.steps.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
        : [],
    }];
  });
}

export function selectPendingPlanReview(
  historicalSnapshots: Array<DisplaySnapshot | undefined>,
  liveSnapshot?: DisplaySnapshot,
): PlanReviewData | undefined {
  const latestById = new Map<string, PlanReviewData>();
  for (const snapshot of [...historicalSnapshots, liveSnapshot]) {
    for (const plan of readPlan(snapshot)) {
      latestById.delete(plan.planId);
      latestById.set(plan.planId, plan);
    }
  }
  return [...latestById.values()].reverse().find((plan) => plan.status === 'review_waiting');
}
