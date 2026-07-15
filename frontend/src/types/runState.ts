/**
 * Chat RunState v2 前端契约。
 *
 * 字段名与后端 run_state_schema.py 保持一致。Chat 主链路只能消费
 * RUN_STATE / RUN_STATE_PATCH，不直接依赖 AgentScope raw SSE。
 */

import type { DisplayEvent, DisplaySnapshot } from './displayEvents';
import type { TeamDomainEvent, TeamSnapshot } from './teamRuntime';

export type RunStateStatus =
  | 'running'
  | 'waiting_user'
  | 'waiting_permission'
  | 'waiting_external'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'cancelling'
  | 'waiting'
  | string;

export type RunStatePatchKind =
  | 'snapshot_meta'
  | 'model_usage'
  | 'approval_resolved'
  | 'display_event'
  | 'team_event'
  | 'notice'
  | 'error';

export interface RunStateToolSummary {
  id: string;
  name: string;
  status: string;
  inputPreview: string;
  outputPreview: string;
  outputSize?: number;
  outputTruncated?: boolean;
  error: string;
  startedAt?: string;
  completedAt?: string;
}

export interface RunStateApprovalSummary {
  interactionId: string;
  kind?: string;
  type: string;
  status: string;
  agentId: string;
  agentName: string;
  toolCalls: Array<{
    id?: string;
    name?: string;
    input?: string;
    rawName?: string;
  }>;
  title?: string;
  description?: string;
  questions?: Array<Record<string, unknown>>;
  externalResultsPreview?: Record<string, string>;
  createdAt: string;
}

export interface ChatRunPlanStep {
  id?: string;
  planId?: string;
  stepIndex?: number;
  title?: string;
  description?: string;
  status?: string;
  assignedAgentId?: string;
  evidence?: string;
  result?: string;
  startedAt?: string;
  completedAt?: string;
  updatedAt?: string;
}

export interface ChatRunPlanState {
  planId: string;
  planFile: string;
  status: string;
  markdown: string;
  steps: ChatRunPlanStep[];
  updatedAt: string;
}

export interface RunStateGeneratedFile {
  path: string;
  name: string;
  extension: string;
}

export interface ChatRunStateSnapshot {
  version?: number;
  runId: string;
  sessionId: string;
  status: RunStateStatus;
  createdAt: string;
  completedAt: string | null;
  reasoning: string;
  finalAnswer: string;
  thinking: string;
  tools: RunStateToolSummary[];
  skillCalls: Array<Record<string, unknown>>;
  modelName: string;
  tokenUsage: { input: number; output: number } | null;
  approvals: RunStateApprovalSummary[];
  plans: ChatRunPlanState[];
  displaySnapshot?: DisplaySnapshot;
  teamSnapshot?: TeamSnapshot | null;
  error: string;
}

export interface ChatRunStatePatch {
  kind: RunStatePatchKind;
  runId: string;
  sessionId: string;
  status: RunStateStatus;
  updatedAt: string;
  completedAt: string | null;
  modelName?: string;
  tokenUsage?: { input: number; output: number } | null;
  displayEvent?: DisplayEvent;
  teamEvent?: TeamDomainEvent;
  resolvedApprovalId?: string;
  notice?: {
    type: string;
    message?: string;
  };
  error?: string;
}

export type { DisplayBlock, DisplayEvent, DisplayItem, DisplaySnapshot } from './displayEvents';
