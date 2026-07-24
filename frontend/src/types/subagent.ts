/**
 * Subagent 相关类型定义。
 *
 * 与 backend/src/server/routes/subagents.py 的响应结构保持同步。
 */

export type SubagentInvocationStatus = 'queued' | 'running' | 'completed' | 'failed' | 'waiting' | 'cancelled' | 'interrupted' | string;
export type SubagentExecutionMode = 'foreground' | 'background' | string;

export interface SubagentTimelineItem {
  id: string;
  kind: string;
  type: string;
  title: string;
  detail: string;
  status: SubagentInvocationStatus;
  timestamp: string;
  toolName?: string;
}

export interface SubagentDefinition {
  name: string;
  description: string;
  scope: string;
  path: string;
  tools: string[] | null;
  disallowedTools: string[];
  model: string;
  permissionMode: string;
  maxTurns: number;
  skills: string[];
  mcpServers: unknown[];
  color: string;
  metadata: Record<string, unknown>;
  contentHash: string;
}

export interface SubagentLoadError {
  scope: string;
  path: string;
  message: string;
}

export interface SubagentCatalogResponse {
  definitions: SubagentDefinition[];
  errors: SubagentLoadError[];
  warnings: string[];
  fingerprint: string;
}

export interface SubagentInvocation {
  id: string;
  runId: string;
  sessionId: string;
  toolCallId: string;
  parentScope: string;
  parentAgentId: string;
  parentAgentName: string;
  definitionScope: string;
  definitionPath: string;
  subagentName: string;
  status: SubagentInvocationStatus;
  executionMode: SubagentExecutionMode;
  task: string;
  expectedOutput: string;
  resultPreview: string;
  error: string;
  startedAt: string;
  completedAt: string | null;
  updatedAt: string;
}

export interface SubagentInvocationListResponse {
  items: SubagentInvocation[];
}

export interface SubagentTranscriptItem {
  id: string;
  invocationId: string;
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface SubagentTranscriptResponse {
  items: SubagentTranscriptItem[];
}
