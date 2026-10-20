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
  const response = await fetch(url, {
    cache: 'no-store',
    signal: AbortSignal.timeout(8_000),
  });
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

  const loadInvocations = useCallback(async () => {
    const invocationList = await requestJson<SubagentInvocationListResponse>(
      `/api/subagents/invocations?limit=${limit}`,
    );
    setInvocations(invocationList.items);
  }, [limit]);

  const load = useCallback(async (showLoading: boolean) => {
    try {
      if (showLoading) setLoading(true);
      setError(null);
      const [catalog] = await Promise.all([
        requestJson<SubagentCatalogResponse>('/api/subagents'),
        loadInvocations(),
      ]);
      setDefinitions(catalog.definitions);
      setErrors(catalog.errors);
      setWarnings(catalog.warnings);
      setFingerprint(catalog.fingerprint);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载 Subagent 数据失败');
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [loadInvocations]);

  const refresh = useCallback(async () => {
    await load(true);
  }, [load]);

  useEffect(() => {
    void load(true);
  }, [load]);

  useEffect(() => {
    const hasActiveTask = invocations.some((invocation) => (
      invocation.status === 'queued'
      || invocation.status === 'running'
      || invocation.status === 'waiting'
    ));
    const intervalMs = hasActiveTask ? 1000 : 2000;
    const timer = window.setInterval(() => {
      void loadInvocations().catch((err) => {
        setError(err instanceof Error ? err.message : '刷新 Subagent 任务失败');
      });
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [invocations, loadInvocations]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void load(false);
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [load]);

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
