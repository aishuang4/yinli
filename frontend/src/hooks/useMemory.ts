/**
 * useMemory — Memory 管理接口 Hook。
 *
 * 封装 Markdown 记忆和长期节点记忆 API。
 */

import { useCallback } from 'react';
import type {
  MarkdownMemoryDocument,
  MarkdownMemoryScope,
  MemoryNodeQueryResult,
  MemoryNodeTreeItem,
} from '../types/memory';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';

interface MemoryNodeFilters {
  agentId?: string;
  query?: string;
  dateFrom?: string;
  dateTo?: string;
  limit?: number;
}

interface UseMemoryReturn {
  readMarkdownMemory: (scope: MarkdownMemoryScope, agentId?: string) => Promise<MarkdownMemoryDocument>;
  saveMarkdownMemory: (scope: MarkdownMemoryScope, content: string, agentId?: string) => Promise<MarkdownMemoryDocument>;
  loadNodeTree: (filters?: MemoryNodeFilters) => Promise<MemoryNodeTreeItem[]>;
  queryNodes: (filters?: MemoryNodeFilters) => Promise<MemoryNodeQueryResult[]>;
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

  return {
    readMarkdownMemory,
    saveMarkdownMemory,
    loadNodeTree,
    queryNodes,
  };
}
