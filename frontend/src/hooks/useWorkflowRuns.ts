/**
 * useWorkflowRuns — Dynamic Workflow 独立数据 Hook。
 *
 * 负责 Workflow Run 列表、详情、创建、启动和事件订阅，不复用 Chat SSE。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { WorkflowEvent, WorkflowRunDetail, WorkflowRunSummary } from '../types/workflow';
import { resolveApiUrl } from '../utils/apiUrl';

interface CreateWorkflowInput {
  title?: string;
  goal: string;
}

export function useWorkflowRuns() {
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<WorkflowRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const loadRuns = useCallback(async () => {
    try {
      setError(null);
      const response = await fetch('/api/workflows');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data: { runs: WorkflowRunSummary[] } = await response.json();
      setRuns(data.runs);
      setSelectedRunId((current) => current || data.runs[0]?.id || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '任务流列表加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (runId: string) => {
    try {
      setDetailLoading(true);
      setError(null);
      const response = await fetch(`/api/workflows/${encodeURIComponent(runId)}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data: WorkflowRunDetail = await response.json();
      setDetail(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : '任务流详情加载失败');
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const createWorkflow = useCallback(async (input: CreateWorkflowInput) => {
    try {
      setCreating(true);
      setError(null);
      const response = await fetch('/api/workflows', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const created: WorkflowRunDetail = await response.json();
      setRuns((current) => [created, ...current.filter((run) => run.id !== created.id)]);
      setSelectedRunId(created.id);
      setDetail(created);
      return created;
    } catch (err) {
      setError(err instanceof Error ? err.message : '任务流创建失败');
      return null;
    } finally {
      setCreating(false);
    }
  }, []);

  const startWorkflow = useCallback(async (runId: string) => {
    try {
      setStarting(true);
      setError(null);
      const response = await fetch(`/api/workflows/${encodeURIComponent(runId)}/start`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await loadDetail(runId);
      await loadRuns();
    } catch (err) {
      setError(err instanceof Error ? err.message : '任务流启动失败');
    } finally {
      setStarting(false);
    }
  }, [loadDetail, loadRuns]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  useEffect(() => {
    if (!selectedRunId) {
      setDetail(null);
      return;
    }
    void loadDetail(selectedRunId);
  }, [loadDetail, selectedRunId]);

  useEffect(() => {
    eventSourceRef.current?.close();
    if (!selectedRunId) return undefined;

    const source = new EventSource(resolveApiUrl(`/api/workflows/${encodeURIComponent(selectedRunId)}/events`));
    eventSourceRef.current = source;
    const handleEvent = (event: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(event.data) as WorkflowEvent;
        setDetail((current) => applyWorkflowEvent(current, parsed));
      } catch {
        // 忽略无法解析的心跳或异常事件。
      }
    };

    [
      'WORKFLOW_CREATED',
      'SCRIPT_REVIEW_REQUIRED',
      'SCRIPT_APPROVED',
      'WORKFLOW_STARTED',
      'STAGE_STARTED',
      'STAGE_COMPLETED',
      'AGENT_TASK_STARTED',
      'AGENT_TASK_COMPLETED',
      'BARRIER_WAITING',
      'VARIABLE_UPDATED',
      'NODE_STARTED',
      'NODE_COMPLETED',
      'NODE_FAILED',
      'WORKFLOW_COMPLETED',
    ].forEach((eventName) => source.addEventListener(eventName, handleEvent));

    source.onerror = () => {
      source.close();
    };

    return () => {
      source.close();
      if (eventSourceRef.current === source) {
        eventSourceRef.current = null;
      }
    };
  }, [selectedRunId]);

  return {
    runs,
    selectedRunId,
    detail,
    loading,
    detailLoading,
    creating,
    starting,
    error,
    setSelectedRunId,
    loadRuns,
    createWorkflow,
    startWorkflow,
  };
}

function applyWorkflowEvent(current: WorkflowRunDetail | null, event: WorkflowEvent): WorkflowRunDetail | null {
  if (!current || current.id !== event.runId) return current;
  const payloadNode = event.payload?.node;
  const payloadScript = event.payload?.script;
  const nextNodes = isWorkflowNodePayload(payloadNode)
    ? current.nodes.map((node) => (node.id === payloadNode.id ? { ...node, ...payloadNode } : node))
    : current.nodes;
  const nextScript = isWorkflowScriptPayload(payloadScript) ? payloadScript : current.script;
  const nextStatus = event.type === 'WORKFLOW_COMPLETED'
    ? 'completed'
    : event.type === 'WORKFLOW_STARTED'
      ? 'running'
      : current.status;
  const currentNodeId = event.type === 'NODE_STARTED'
    || event.type === 'STAGE_STARTED'
    ? event.nodeId
    : event.type === 'WORKFLOW_COMPLETED'
      ? ''
      : current.currentNodeId;
  return {
    ...current,
    status: nextStatus,
    currentNodeId,
    script: nextScript,
    nodes: nextNodes,
    events: [...current.events.filter((item) => item.id !== event.id), event].sort((a, b) => a.sortOrder - b.sortOrder),
    eventCount: Math.max(current.eventCount, event.sortOrder + 1),
    updatedAt: event.createdAt,
    completedAt: event.type === 'WORKFLOW_COMPLETED' ? event.createdAt : current.completedAt,
  };
}

function isWorkflowNodePayload(value: unknown): value is WorkflowRunDetail['nodes'][number] {
  return Boolean(value && typeof value === 'object' && 'id' in value && 'status' in value);
}

function isWorkflowScriptPayload(value: unknown): value is NonNullable<WorkflowRunDetail['script']> {
  return Boolean(value && typeof value === 'object' && 'schemaVersion' in value && 'stages' in value);
}
