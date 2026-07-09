/**
 * Dynamic Workflow 前端类型。
 *
 * Workflow 是独立于 Chat 的状态优先模块，前端只依赖这些稳定字段恢复执行舞台。
 */

export type WorkflowRunStatus = 'draft' | 'running' | 'waiting' | 'failed' | 'completed' | 'cancelled';
export type WorkflowNodeStatus = 'pending' | 'running' | 'waiting' | 'failed' | 'completed' | 'skipped';

export interface WorkflowScriptStage {
  id: string;
  type: string;
  title: string;
  instruction: string;
  input?: string;
  collectAs?: string;
  items?: Array<{
    key: string;
    agentId: string;
    agentName: string;
    instruction: string;
  }>;
  outputSchema?: Record<string, string>;
}

export interface WorkflowScript {
  schemaVersion: string;
  title: string;
  goal: string;
  summary: string;
  concurrency: {
    maxAgents: number;
    failurePolicy: string;
    retryLimit: number;
  };
  estimate: {
    agents: number;
    tokens: number;
    minutes: number;
    riskLevel: string;
  };
  permissions: {
    filesystem: string;
    network: string;
    writes: string;
  };
  stages: WorkflowScriptStage[];
}

export interface WorkflowRunSummary {
  id: string;
  schemaVersion: string;
  title: string;
  goal: string;
  status: WorkflowRunStatus | string;
  currentNodeId: string;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
  eventCount: number;
}

export interface WorkflowNode {
  id: string;
  runId: string;
  key: string;
  type: string;
  title: string;
  instruction: string;
  status: WorkflowNodeStatus | string;
  agentId: string;
  teamId: string;
  summary: string;
  output: string;
  error: string;
  position: number;
  metadata: Record<string, unknown>;
  startedAt: string | null;
  completedAt: string | null;
  updatedAt: string;
}

export interface WorkflowEdge {
  id: string;
  runId: string;
  fromNodeId: string;
  toNodeId: string;
  condition: string;
  createdAt: string;
}

export interface WorkflowEvent {
  id: string;
  runId: string;
  nodeId: string;
  type: string;
  status: string;
  title: string;
  content: string;
  payload: Record<string, unknown>;
  createdAt: string;
  sortOrder: number;
}

export interface WorkflowRunDetail extends WorkflowRunSummary {
  script?: WorkflowScript;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  events: WorkflowEvent[];
  artifacts: Array<{
    id: string;
    runId: string;
    nodeId: string;
    type: string;
    title: string;
    content: string;
    uri: string;
    metadata: Record<string, unknown>;
    createdAt: string;
  }>;
}
