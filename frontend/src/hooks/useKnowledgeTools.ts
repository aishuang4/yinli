import { useCallback, useEffect, useState } from 'react';
import type { KnowledgeTool, KnowledgeToolPayload } from '../types/tool';

interface UseKnowledgeToolsReturn {
  tools: KnowledgeTool[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createTool: (payload: KnowledgeToolPayload) => Promise<KnowledgeTool>;
  updateTool: (id: string, payload: Partial<KnowledgeToolPayload>) => Promise<KnowledgeTool>;
  deleteTool: (id: string) => Promise<void>;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function useKnowledgeTools(): UseKnowledgeToolsReturn {
  const [tools, setTools] = useState<KnowledgeTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setTools(await requestJson<KnowledgeTool[]>('/api/tools/knowledge-base'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load knowledge tools');
    } finally {
      setLoading(false);
    }
  }, []);

  const upsertLocal = useCallback((tool: KnowledgeTool) => {
    setTools((prev) => (
      prev.some((item) => item.id === tool.id)
        ? prev.map((item) => (item.id === tool.id ? tool : item))
        : [...prev, tool]
    ));
  }, []);

  const createTool = useCallback(async (payload: KnowledgeToolPayload) => {
    const tool = await requestJson<KnowledgeTool>('/api/tools/knowledge-base', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    upsertLocal(tool);
    return tool;
  }, [upsertLocal]);

  const updateTool = useCallback(async (id: string, payload: Partial<KnowledgeToolPayload>) => {
    const tool = await requestJson<KnowledgeTool>(`/api/tools/knowledge-base/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    upsertLocal(tool);
    return tool;
  }, [upsertLocal]);

  const deleteTool = useCallback(async (id: string) => {
    const response = await fetch(`/api/tools/knowledge-base/${id}`, { method: 'DELETE' });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    setTools((prev) => prev.filter((tool) => tool.id !== id));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { tools, loading, error, refresh, createTool, updateTool, deleteTool };
}
