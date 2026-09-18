/**
 * traceTypes — 按 Chat Run 组织的 Observation 时间线。
 */

export type ObservationKind =
  | 'run'
  | 'generation'
  | 'tool'
  | 'skill'
  | 'approval'
  | 'subagent'
  | 'team-task';

export interface TraceSummary {
  id: string;
  sessionId: string;
  workspaceId: string;
  projectId: string;
  title: string;
  agentId: string | null;
  agentName: string | null;
  mode: 'agent' | 'team' | string;
  status: string;
  durationMs: number | null;
  observationCount: number;
  toolCount: number;
  createdAt: string;
  completedAt: string | null;
  updatedAt: string;
}

export interface TraceObservation {
  id: string;
  runId: string;
  sessionId: string;
  workspaceId: string;
  projectId: string;
  kind: ObservationKind | string;
  name: string;
  parentId: string | null;
  status: string;
  startedAt: string;
  endedAt: string | null;
  durationMs: number | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface TraceDetail extends TraceSummary {
  observations: TraceObservation[];
}

export const ACTIVE_TRACE_STATUSES = new Set([
  'running',
  'waiting',
  'waiting_user',
  'waiting_permission',
  'waiting_external',
]);
