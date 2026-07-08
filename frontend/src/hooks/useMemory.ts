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

function markdownUrl(scope: MarkdownMemoryScope, agentId?: string): string {
  if (scope === 'agent') {
    return `/api/memory/markdown/agent/${encodeURIComponent(agentId || 'default')}`;
  }
  if (scope === 'global') {
    return '/api/memory/markdown/global';
  }
  return '/api/memory/markdown/user';
}

function nodeQueryParams(filters: MemoryNodeFilters = {}): string {
  const params = new URLSearchParams();
  if (filters.agentId) params.set('agentId', filters.agentId);
  if (filters.query) params.set('query', filters.query);
  if (filters.dateFrom) params.set('dateFrom', filters.dateFrom);
  if (filters.dateTo) params.set('dateTo', filters.dateTo);
  if (filters.status) params.set('status', filters.status);
  if (filters.limit) params.set('limit', String(filters.limit));
  return params.toString();
}

function skillEvolutionQueryParams(filters: SkillEvolutionFilters = {}): string {
  const params = new URLSearchParams();
  if (filters.status && filters.status !== 'all') params.set('status', filters.status);
  if (filters.agentId) params.set('agentId', filters.agentId);
  if (filters.limit) params.set('limit', String(filters.limit));
  return params.toString();
}

export function useMemory(): UseMemoryReturn {
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
    const params = nodeQueryParams({ ...filters, limit: filters.limit || 30 });
    const data = await requestJson<{ nodes: MemoryNodeTreeItem[] }>(
      `/api/memory/nodes/tree${params ? `?${params}` : ''}`,
    );
    return data.nodes;
  }, []);

  const queryNodes = useCallback(async (filters: MemoryNodeFilters = {}) => {
    const params = nodeQueryParams({ ...filters, limit: filters.limit || 20 });
    const data = await requestJson<{ results: MemoryNodeQueryResult[] }>(
      `/api/memory/nodes/query${params ? `?${params}` : ''}`,
    );
    return data.results;
  }, []);

  const updateNodeStatus = useCallback(async (
    nodeId: string,
    status: MemoryNodeStatus,
    reason = '',
  ) => {
    const result = await requestJson<MemoryNodeQueryResult>(
      `/api/memory/nodes/${encodeURIComponent(nodeId)}/status`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, reason }),
      },
    );
    notifySuccess(status === 'active' ? '历史记忆已恢复' : '历史记忆状态已更新');
    return result;
  }, []);

  const loadSkillEvolutionRecords = useCallback(async (filters: SkillEvolutionFilters = {}) => {
    const params = skillEvolutionQueryParams({ ...filters, limit: filters.limit || 50 });
    const data = await requestJson<{ records: SkillEvolutionRecord[] }>(
      `/api/memory/skill-evolution/records${params ? `?${params}` : ''}`,
    );
    return data.records;
  }, []);

  const updateSkillEvolutionRecord = useCallback(async (
    recordId: string,
    action: SkillEvolutionRecordAction,
    reason = '',
  ) => {
    const result = await requestJson<SkillEvolutionRecord>(
      `/api/memory/skill-evolution/records/${encodeURIComponent(recordId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, reason }),
      },
    );
    notifySuccess('Skill 队列已更新');
    return result;
  }, []);

  const loadMemoryAuditEvents = useCallback(async (limit = 50) => {
    const params = new URLSearchParams({ limit: String(limit) });
    const data = await requestJson<{ events: MemoryAuditEvent[] }>(
      `/api/memory/audit/events?${params.toString()}`,
    );
    return data.events;
  }, []);

  const sendRetrievalFeedback = useCallback(async (payload: MemoryRetrievalFeedbackPayload) => {
    await requestJson<{ ok: boolean }>('/api/memory/retrieval/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    notifySuccess('召回反馈已记录');
  }, []);

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
    loadMemoryAuditEvents,
    sendRetrievalFeedback,
    loadMemorySettings,
    saveAdmissionPolicy,
    syncWikiVault,
    loadWikiGraph,
  };
}
