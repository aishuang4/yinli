/**
 * traceTypes — 运行轨迹页面类型定义。
 */

import type { TeamActorInfo, ToolCallInfo } from '../../hooks/useEventStream';

export interface TraceSummary {
  id: string;
  title: string;
  agentId: string | null;
  agentName: string | null;
  teamId: string | null;
  teamName: string | null;
  messageCount: number;
  toolCallCount: number;
  workerCount: number;
  status: string;
  createdAt: string;
  updatedAt: string;
}

export interface TraceMessage {
  id: string;
  role: string;
  content: string;
  agentId: string | null;
  agentName: string | null;
  modelName: string | null;
  error: string | null;
  toolCalls: ToolCallInfo[];
  teamActivities: TeamActorInfo[];
  createdAt: string;
}

export interface TraceEvent {
  id: string;
  sessionId: string;
  eventType: string;
  scope: string;
  agentId: string | null;
  agentName: string | null;
  teamActivityId: string | null;
  status: string;
  title: string;
  content: string;
  payload: Record<string, unknown>;
  createdAt: string;
  sortOrder: number;
}

export interface TraceDetail extends TraceSummary {
  messages: TraceMessage[];
  events: TraceEvent[];
  eventTotal: number;
}

export type TraceEventFilter = 'all' | 'runtime' | 'permissions' | 'tools' | 'models' | 'hitl';
