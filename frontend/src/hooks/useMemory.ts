/**
 * useMemory — Memory 管理接口 Hook。
 *
 * 封装 Markdown 记忆和长期节点记忆 API。
 */

import { useCallback } from 'react';
import type {
  MarkdownMemoryDocument,
  MarkdownMemoryScope,
  MemoryAuditEvent,
  MemoryCandidateAction,
  MemoryCandidateStatus,
  MemoryMarkdownCandidate,
  MemoryAdmissionPolicyUpdate,
  MemoryNodeStatus,
  MemoryRetrievalFeedbackPayload,
  MemorySettingsSnapshot,
  MemoryNodeQueryResult,
  MemoryNodeTreeItem,
  TimelineQuery,
  TimelineQueryResult,
} from '../types/memory';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';
import { useAppStore } from '../stores/appStore';

interface MemoryNodeFilters {
  agentId?: string;
  query?: string;
  dateFrom?: string;
  dateTo?: string;
  status?: MemoryNodeStatus | 'all';
  limit?: number;
}

interface UseMemoryReturn {
  readMarkdownMemory: (scope: MarkdownMemoryScope, agentId?: string) => Promise<MarkdownMemoryDocument>;
  saveMarkdownMemory: (scope: MarkdownMemoryScope, content: string, agentId?: string) => Promise<MarkdownMemoryDocument>;
  loadNodeTree: (filters?: MemoryNodeFilters) => Promise<MemoryNodeTreeItem[]>;
  queryNodes: (filters?: MemoryNodeFilters) => Promise<MemoryNodeQueryResult[]>;
  updateNodeStatus: (nodeId: string, status: MemoryNodeStatus, reason?: string) => Promise<MemoryNodeQueryResult>;
  loadMemoryCandidates: (status?: MemoryCandidateStatus | 'all') => Promise<{ records: MemoryMarkdownCandidate[]; pendingCount: number }>;
  updateMemoryCandidate: (recordId: string, action: MemoryCandidateAction, reason?: string) => Promise<MemoryMarkdownCandidate>;
  loadMemoryAuditEvents: (limit?: number) => Promise<MemoryAuditEvent[]>;
  sendRetrievalFeedback: (payload: MemoryRetrievalFeedbackPayload) => Promise<void>;
  loadMemorySettings: () => Promise<MemorySettingsSnapshot>;
  saveAdmissionPolicy: (payload: MemoryAdmissionPolicyUpdate) => Promise<MemorySettingsSnapshot['admission']>;
  readHabits: () => Promise<MarkdownMemoryDocument>;
  saveHabits: (content: string) => Promise<MarkdownMemoryDocument>;
  readProfessional: (agentId: string) => Promise<MarkdownMemoryDocument>;
  saveProfessional: (agentId: string, content: string) => Promise<MarkdownMemoryDocument>;
  loadTimeline: (filters?: TimelineQuery) => Promise<TimelineQueryResult>;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function markdownUrl(scope: MarkdownMemoryScope, agentId?: string): string {
  if (scope === 'agent') {
    return `/api/memory/markdown/agent/${encodeURIComponent(agentId || 'default')}`;
  }
  if (scope === 'global') {
    return '/api/memory/markdown/global';
  }
  return '/api/memory/markdown/user';
}

function nodeQueryParams(
  filters: MemoryNodeFilters = {},
  projectSlug?: string,
): string {
  const params = new URLSearchParams();
  if (projectSlug) {
    params.set('projectSlug', projectSlug);
  }
  if (filters.agentId) params.set('agentId', filters.agentId);
  if (filters.query) params.set('query', filters.query);
  if (filters.dateFrom) params.set('dateFrom', filters.dateFrom);
  if (filters.dateTo) params.set('dateTo', filters.dateTo);
  if (filters.status) params.set('status', filters.status);
  if (filters.limit) params.set('limit', String(filters.limit));
  return params.toString();
}

export function useMemory(): UseMemoryReturn {
    const activeProjectSlug = useAppStore((state) => state.activeProjectSlug);
  const readMarkdownMemory = useCallback(async (scope: MarkdownMemoryScope, agentId?: string) => (
    requestJson<MarkdownMemoryDocument>(markdownUrl(scope, agentId))
  ), []);

  const saveMarkdownMemory = useCallback(async (
    scope: MarkdownMemoryScope,
    content: string,
    agentId?: string,
  ) => {
    try {
      const doc = await requestJson<MarkdownMemoryDocument>(markdownUrl(scope, agentId), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      });
      notifySuccess('记忆已保存');
      return doc;
    } catch (error) {
      notifyError(`记忆保存失败：${getErrorMessage(error, '未知错误')}`);
      throw error;
    }
  }, []);

  const loadNodeTree = useCallback(async (filters: MemoryNodeFilters = {}) => {
    const params = nodeQueryParams(
      { ...filters, limit: filters.limit || 30 },
      activeProjectSlug,
    );
    const data = await requestJson<{ nodes: MemoryNodeTreeItem[] }>(
      `/api/memory/nodes/tree${params ? `?${params}` : ''}`,
    );
    return data.nodes;
  }, [activeProjectSlug]);

  const queryNodes = useCallback(async (filters: MemoryNodeFilters = {}) => {
    const params = nodeQueryParams(
      { ...filters, limit: filters.limit || 20 },
      activeProjectSlug,
    );
    const data = await requestJson<{ results: MemoryNodeQueryResult[] }>(
      `/api/memory/nodes/query${params ? `?${params}` : ''}`,
    );
    return data.results;
  }, [activeProjectSlug]);

  const updateNodeStatus = useCallback(async (
    nodeId: string,
    status: MemoryNodeStatus,
    reason = '',
  ) => {
    const result = await requestJson<MemoryNodeQueryResult>(
      `/api/memory/nodes/${encodeURIComponent(nodeId)}/status?projectSlug=${encodeURIComponent(activeProjectSlug)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, reason }),
      },
    );
    notifySuccess(status === 'active' ? '历史记忆已恢复' : '历史记忆状态已更新');
    return result;
  }, [activeProjectSlug]);

  const loadMemoryCandidates = useCallback(async (status: MemoryCandidateStatus | 'all' = 'pending') => {
    const params = new URLSearchParams({ projectSlug: activeProjectSlug, limit: '100' });
    if (status !== 'all') params.set('status', status);
    return requestJson<{ records: MemoryMarkdownCandidate[]; pendingCount: number }>(
      `/api/memory/markdown-candidates?${params.toString()}`,
    );
  }, [activeProjectSlug]);

  const updateMemoryCandidate = useCallback(async (
    recordId: string,
    action: MemoryCandidateAction,
    reason = '',
  ) => {
    const result = await requestJson<MemoryMarkdownCandidate>(
      `/api/memory/markdown-candidates/${encodeURIComponent(recordId)}?projectSlug=${encodeURIComponent(activeProjectSlug)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, reason }),
      },
    );
    notifySuccess(
      result.status === 'accepted'
        ? '记忆候选已采纳并写入'
        : result.status === 'rejected'
          ? '记忆候选已拒绝'
          : `记忆候选处理失败：${result.resolution || '未知原因'}`,
    );
    return result;
  }, [activeProjectSlug]);

  const loadMemoryAuditEvents = useCallback(async (limit = 50) => {
    const params = new URLSearchParams({
      projectSlug: activeProjectSlug,
      limit: String(limit),
    });
    const data = await requestJson<{ events: MemoryAuditEvent[] }>(
      `/api/memory/audit/events?${params.toString()}`,
    );
    return data.events;
  }, [activeProjectSlug]);

  const sendRetrievalFeedback = useCallback(async (payload: MemoryRetrievalFeedbackPayload) => {
    await requestJson<{ ok: boolean }>('/api/memory/retrieval/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, projectSlug: activeProjectSlug }),
    });
    notifySuccess('召回反馈已记录');
  }, [activeProjectSlug]);

  const loadMemorySettings = useCallback(async () => (
    requestJson<MemorySettingsSnapshot>('/api/memory/settings')
  ), []);

  const saveAdmissionPolicy = useCallback(async (payload: MemoryAdmissionPolicyUpdate) => {
    try {
      const result = await requestJson<MemorySettingsSnapshot['admission']>('/api/memory/settings/admission', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      notifySuccess('准入策略已保存');
      return result;
    } catch (error) {
      notifyError(`准入策略保存失败：${getErrorMessage(error, '未知错误')}`);
      throw error;
    }
  }, []);

  const readHabits = useCallback(async () => {
    return requestJson<MarkdownMemoryDocument>('/api/memory/habits');
  }, []);

  const saveHabits = useCallback(async (content: string) => {
    const doc = await requestJson<MarkdownMemoryDocument>('/api/memory/habits', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    notifySuccess('习惯已保存');
    return doc;
  }, []);

  const readProfessional = useCallback(async (agentId: string) => {
    return requestJson<MarkdownMemoryDocument>(`/api/memory/professional/${encodeURIComponent(agentId)}`);
  }, []);

  const saveProfessional = useCallback(async (agentId: string, content: string) => {
    const doc = await requestJson<MarkdownMemoryDocument>(
      `/api/memory/professional/${encodeURIComponent(agentId)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      },
    );
    notifySuccess('专业区已保存');
    return doc;
  }, []);

  const loadTimeline = useCallback(async (filters: TimelineQuery = {}) => {
    const params = new URLSearchParams();
    const projectSlug = filters.projectSlug ?? activeProjectSlug;
    if (projectSlug) params.set('projectSlug', projectSlug);
    if (filters.agentId) params.set('agentId', filters.agentId);
    if (filters.surface) params.set('surface', filters.surface);
    if (filters.timeFrom) params.set('timeFrom', filters.timeFrom);
    if (filters.timeTo) params.set('timeTo', filters.timeTo);
    if (filters.queryText) params.set('queryText', filters.queryText);
    return requestJson<TimelineQueryResult>(`/api/memory/timeline?${params.toString()}`);
  }, [activeProjectSlug]);

  return {
    readMarkdownMemory,
    saveMarkdownMemory,
    readHabits,
    saveHabits,
    readProfessional,
    saveProfessional,
    loadTimeline,
    loadNodeTree,
    queryNodes,
    updateNodeStatus,
    loadMemoryCandidates,
    updateMemoryCandidate,
    loadMemoryAuditEvents,
    sendRetrievalFeedback,
    loadMemorySettings,
    saveAdmissionPolicy,
  };
}
