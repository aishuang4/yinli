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
  MemoryWikiGraph,
  MemoryWikiSyncResult,
  MemoryNodeQueryResult,
  MemoryNodeTreeItem,
  SkillEvolutionRecordAction,
  SkillEvolutionRecord,
  SkillEvolutionRecordStatus,
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

interface SkillEvolutionFilters {
  status?: SkillEvolutionRecordStatus | 'all';
  agentId?: string;
  limit?: number;
}

interface UseMemoryReturn {
  readMarkdownMemory: (scope: MarkdownMemoryScope, agentId?: string) => Promise<MarkdownMemoryDocument>;
  saveMarkdownMemory: (scope: MarkdownMemoryScope, content: string, agentId?: string) => Promise<MarkdownMemoryDocument>;
  loadNodeTree: (filters?: MemoryNodeFilters) => Promise<MemoryNodeTreeItem[]>;
  queryNodes: (filters?: MemoryNodeFilters) => Promise<MemoryNodeQueryResult[]>;
  updateNodeStatus: (nodeId: string, status: MemoryNodeStatus, reason?: string) => Promise<MemoryNodeQueryResult>;
  loadSkillEvolutionRecords: (filters?: SkillEvolutionFilters) => Promise<SkillEvolutionRecord[]>;
  updateSkillEvolutionRecord: (
    recordId: string,
    action: SkillEvolutionRecordAction,
    reason?: string,
  ) => Promise<SkillEvolutionRecord>;
  loadMemoryCandidates: (status?: MemoryCandidateStatus | 'all') => Promise<{ records: MemoryMarkdownCandidate[]; pendingCount: number }>;
  updateMemoryCandidate: (recordId: string, action: MemoryCandidateAction, reason?: string) => Promise<MemoryMarkdownCandidate>;
  loadMemoryAuditEvents: (limit?: number) => Promise<MemoryAuditEvent[]>;
  sendRetrievalFeedback: (payload: MemoryRetrievalFeedbackPayload) => Promise<void>;
  loadMemorySettings: () => Promise<MemorySettingsSnapshot>;
  saveAdmissionPolicy: (payload: MemoryAdmissionPolicyUpdate) => Promise<MemorySettingsSnapshot['admission']>;
  syncWikiVault: (historyLimit?: number) => Promise<MemoryWikiSyncResult>;
  loadWikiGraph: (historyLimit?: number, edgeLimit?: number) => Promise<MemoryWikiGraph>;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function markdownUrl(scope: MarkdownMemoryScope, agentId?: string, workspaceId = 'default'): string {
  if (scope === 'agent') {
    return `/api/memory/markdown/agent/${encodeURIComponent(agentId || 'default')}`;
  }
  if (scope === 'global') {
    return workspaceId === 'default'
      ? '/api/memory/markdown/global'
      : `/api/memory/markdown/global/${encodeURIComponent(workspaceId)}`;
  }
  return '/api/memory/markdown/user';
}

function nodeQueryParams(
  filters: MemoryNodeFilters = {},
  context?: { workspaceId: string; projectId: string },
): string {
  const params = new URLSearchParams();
  if (context) {
    params.set('workspaceId', context.workspaceId);
    params.set('projectId', context.projectId);
  }
  if (filters.agentId) params.set('agentId', filters.agentId);
  if (filters.query) params.set('query', filters.query);
  if (filters.dateFrom) params.set('dateFrom', filters.dateFrom);
  if (filters.dateTo) params.set('dateTo', filters.dateTo);
  if (filters.status) params.set('status', filters.status);
  if (filters.limit) params.set('limit', String(filters.limit));
  return params.toString();
}

function skillEvolutionQueryParams(filters: SkillEvolutionFilters = {}, projectId?: string): string {
  const params = new URLSearchParams();
  if (projectId) params.set('projectId', projectId);
  if (filters.status && filters.status !== 'all') params.set('status', filters.status);
  if (filters.agentId) params.set('agentId', filters.agentId);
  if (filters.limit) params.set('limit', String(filters.limit));
  return params.toString();
}

export function useMemory(): UseMemoryReturn {
  const activeWorkspaceId = useAppStore((state) => state.activeWorkspaceId);
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const readMarkdownMemory = useCallback(async (scope: MarkdownMemoryScope, agentId?: string) => (
    requestJson<MarkdownMemoryDocument>(markdownUrl(scope, agentId, activeWorkspaceId))
  ), [activeWorkspaceId]);

  const saveMarkdownMemory = useCallback(async (
    scope: MarkdownMemoryScope,
    content: string,
    agentId?: string,
  ) => {
    try {
      const doc = await requestJson<MarkdownMemoryDocument>(markdownUrl(scope, agentId, activeWorkspaceId), {
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
  }, [activeWorkspaceId]);

  const loadNodeTree = useCallback(async (filters: MemoryNodeFilters = {}) => {
    const params = nodeQueryParams(
      { ...filters, limit: filters.limit || 30 },
      { workspaceId: activeWorkspaceId, projectId: activeProjectId },
    );
    const data = await requestJson<{ nodes: MemoryNodeTreeItem[] }>(
      `/api/memory/nodes/tree${params ? `?${params}` : ''}`,
    );
    return data.nodes;
  }, [activeProjectId, activeWorkspaceId]);

  const queryNodes = useCallback(async (filters: MemoryNodeFilters = {}) => {
    const params = nodeQueryParams(
      { ...filters, limit: filters.limit || 20 },
      { workspaceId: activeWorkspaceId, projectId: activeProjectId },
    );
    const data = await requestJson<{ results: MemoryNodeQueryResult[] }>(
      `/api/memory/nodes/query${params ? `?${params}` : ''}`,
    );
    return data.results;
  }, [activeProjectId, activeWorkspaceId]);

  const updateNodeStatus = useCallback(async (
    nodeId: string,
    status: MemoryNodeStatus,
    reason = '',
  ) => {
    const result = await requestJson<MemoryNodeQueryResult>(
      `/api/memory/nodes/${encodeURIComponent(nodeId)}/status?projectId=${encodeURIComponent(activeProjectId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, reason }),
      },
    );
    notifySuccess(status === 'active' ? '历史记忆已恢复' : '历史记忆状态已更新');
    return result;
  }, [activeProjectId]);

  const loadSkillEvolutionRecords = useCallback(async (filters: SkillEvolutionFilters = {}) => {
    const params = skillEvolutionQueryParams(
      { ...filters, limit: filters.limit || 50 },
      activeProjectId,
    );
    const data = await requestJson<{ records: SkillEvolutionRecord[] }>(
      `/api/memory/skill-evolution/records${params ? `?${params}` : ''}`,
    );
    return data.records;
  }, [activeProjectId]);

  const updateSkillEvolutionRecord = useCallback(async (
    recordId: string,
    action: SkillEvolutionRecordAction,
    reason = '',
  ) => {
    const result = await requestJson<SkillEvolutionRecord>(
      `/api/memory/skill-evolution/records/${encodeURIComponent(recordId)}?projectId=${encodeURIComponent(activeProjectId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, reason }),
      },
    );
    notifySuccess('Skill 队列已更新');
    return result;
  }, [activeProjectId]);

  const loadMemoryCandidates = useCallback(async (status: MemoryCandidateStatus | 'all' = 'pending') => {
    const params = new URLSearchParams({ projectId: activeProjectId, limit: '100' });
    if (status !== 'all') params.set('status', status);
    return requestJson<{ records: MemoryMarkdownCandidate[]; pendingCount: number }>(
      `/api/memory/markdown-candidates?${params.toString()}`,
    );
  }, [activeProjectId]);

  const updateMemoryCandidate = useCallback(async (
    recordId: string,
    action: MemoryCandidateAction,
    reason = '',
  ) => {
    const result = await requestJson<MemoryMarkdownCandidate>(
      `/api/memory/markdown-candidates/${encodeURIComponent(recordId)}?projectId=${encodeURIComponent(activeProjectId)}`,
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
  }, [activeProjectId]);

  const loadMemoryAuditEvents = useCallback(async (limit = 50) => {
    const params = new URLSearchParams({
      projectId: activeProjectId,
      limit: String(limit),
    });
    const data = await requestJson<{ events: MemoryAuditEvent[] }>(
      `/api/memory/audit/events?${params.toString()}`,
    );
    return data.events;
  }, [activeProjectId]);

  const sendRetrievalFeedback = useCallback(async (payload: MemoryRetrievalFeedbackPayload) => {
    await requestJson<{ ok: boolean }>('/api/memory/retrieval/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, projectId: activeProjectId }),
    });
    notifySuccess('召回反馈已记录');
  }, [activeProjectId]);

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

  const syncWikiVault = useCallback(async (historyLimit = 200) => {
    try {
      const params = new URLSearchParams({ historyLimit: String(historyLimit) });
      const result = await requestJson<MemoryWikiSyncResult>(`/api/memory/wiki/sync?${params.toString()}`, {
        method: 'POST',
      });
      notifySuccess(`Memory Wiki 已同步：${result.filesWritten} 个文件`);
      return result;
    } catch (error) {
      notifyError(`Memory Wiki 同步失败：${getErrorMessage(error, '未知错误')}`);
      throw error;
    }
  }, []);

  const loadWikiGraph = useCallback(async (historyLimit = 200, edgeLimit = 300) => {
    const params = new URLSearchParams({
      historyLimit: String(historyLimit),
      edgeLimit: String(edgeLimit),
    });
    return requestJson<MemoryWikiGraph>(`/api/memory/wiki/graph?${params.toString()}`);
  }, []);

  return {
    readMarkdownMemory,
    saveMarkdownMemory,
    loadNodeTree,
    queryNodes,
    updateNodeStatus,
    loadSkillEvolutionRecords,
    updateSkillEvolutionRecord,
    loadMemoryCandidates,
    updateMemoryCandidate,
    loadMemoryAuditEvents,
    sendRetrievalFeedback,
    loadMemorySettings,
    saveAdmissionPolicy,
    syncWikiVault,
    loadWikiGraph,
  };
}
