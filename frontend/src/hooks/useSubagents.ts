/**
 * useSubagents — Subagent 定义和 invocation 审计 Hook。
 *
 * 封装 /api/subagents 相关读取接口，供管理页展示文件定义与委托历史。
 */

import { useCallback, useEffect, useState } from 'react';
import type {
  SubagentCatalogResponse,
  SubagentDefinition,
  SubagentInvocation,
  SubagentInvocationListResponse,
  SubagentLoadError,
} from '../types/subagent';

interface UseSubagentsReturn {
  definitions: SubagentDefinition[];
  invocations: SubagentInvocation[];
  errors: SubagentLoadError[];
  warnings: string[];
  fingerprint: string;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

async function requestJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function useSubagents(limit = 100): UseSubagentsReturn {
  const [definitions, setDefinitions] = useState<SubagentDefinition[]>([]);
  const [invocations, setInvocations] = useState<SubagentInvocation[]>([]);
  const [errors, setErrors] = useState<SubagentLoadError[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [fingerprint, setFingerprint] = useState('empty');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [catalog, invocationList] = await Promise.all([
        requestJson<SubagentCatalogResponse>('/api/subagents'),
        requestJson<SubagentInvocationListResponse>(`/api/subagents/invocations?limit=${limit}`),
      ]);
      setDefinitions(catalog.definitions);
      setErrors(catalog.errors);
      setWarnings(catalog.warnings);
      setFingerprint(catalog.fingerprint);
      setInvocations(invocationList.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载 Subagent 数据失败');
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return {
    definitions,
    invocations,
    errors,
    warnings,
    fingerprint,
    loading,
    error,
    refresh,
  };
}
