import { useCallback, useEffect, useState } from 'react';
import type { HttpTool, HttpToolPayload, KnowledgeTool, KnowledgeToolPayload, SystemTool } from '../types/tool';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';
import { useAppStore } from '../stores/appStore';
import { bindProjectResource } from '../utils/projectResources';

interface UseKnowledgeToolsReturn {
  tools: KnowledgeTool[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createTool: (payload: KnowledgeToolPayload) => Promise<KnowledgeTool>;
  updateTool: (id: string, payload: Partial<KnowledgeToolPayload>) => Promise<KnowledgeTool>;
  deleteTool: (id: string) => Promise<void>;
}

interface UseHttpToolsReturn {
  tools: HttpTool[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createTool: (payload: HttpToolPayload) => Promise<HttpTool>;
  updateTool: (id: string, payload: Partial<HttpToolPayload>) => Promise<HttpTool>;
  deleteTool: (id: string) => Promise<void>;
}

interface UseSystemToolsReturn {
  tools: SystemTool[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
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
    try {
      const tool = await requestJson<KnowledgeTool>('/api/tools/knowledge-base', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      upsertLocal(tool);
      notifySuccess('知识库工具创建成功');
      return tool;
    } catch (err) {
      notifyError(`知识库工具创建失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [upsertLocal]);

  const updateTool = useCallback(async (id: string, payload: Partial<KnowledgeToolPayload>) => {
    try {
      const tool = await requestJson<KnowledgeTool>(`/api/tools/knowledge-base/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      upsertLocal(tool);
      notifySuccess('知识库工具保存成功');
      return tool;
    } catch (err) {
      notifyError(`知识库工具保存失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [upsertLocal]);

  const deleteTool = useCallback(async (id: string) => {
    try {
      const response = await fetch(`/api/tools/knowledge-base/${id}`, { method: 'DELETE' });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }
      setTools((prev) => prev.filter((tool) => tool.id !== id));
      notifySuccess('知识库工具删除成功');
    } catch (err) {
      notifyError(`知识库工具删除失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { tools, loading, error, refresh, createTool, updateTool, deleteTool };
}

export function useHttpTools(): UseHttpToolsReturn {
  const activeWorkspaceId = useAppStore((state) => state.activeWorkspaceId);
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const [tools, setTools] = useState<HttpTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setTools(await requestJson<HttpTool[]>(
        `/api/tools/http?workspaceId=${encodeURIComponent(activeWorkspaceId)}`,
      ));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load HTTP tools');
    } finally {
      setLoading(false);
    }
  }, [activeWorkspaceId]);

  const upsertLocal = useCallback((tool: HttpTool) => {
    setTools((prev) => (
      prev.some((item) => item.id === tool.id)
        ? prev.map((item) => (item.id === tool.id ? tool : item))
        : [tool, ...prev]
    ));
  }, []);

  const createTool = useCallback(async (payload: HttpToolPayload) => {
    try {
      const tool = await requestJson<HttpTool>('/api/tools/http', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, workspaceId: activeWorkspaceId }),
      });
      await bindProjectResource(activeProjectId, 'http_tool', tool.id);
      upsertLocal(tool);
      notifySuccess('HTTP 工具创建成功');
      return tool;
    } catch (err) {
      notifyError(`HTTP 工具创建失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [activeProjectId, activeWorkspaceId, upsertLocal]);

  const updateTool = useCallback(async (id: string, payload: Partial<HttpToolPayload>) => {
    try {
      const tool = await requestJson<HttpTool>(`/api/tools/http/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      upsertLocal(tool);
      notifySuccess('HTTP 工具保存成功');
      return tool;
    } catch (err) {
      notifyError(`HTTP 工具保存失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [upsertLocal]);

  const deleteTool = useCallback(async (id: string) => {
    try {
      const response = await fetch(`/api/tools/http/${id}`, { method: 'DELETE' });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }
      setTools((prev) => prev.filter((tool) => tool.id !== id));
      notifySuccess('HTTP 工具删除成功');
    } catch (err) {
      notifyError(`HTTP 工具删除失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { tools, loading, error, refresh, createTool, updateTool, deleteTool };
}

export function useSystemTools(): UseSystemToolsReturn {
  const [tools, setTools] = useState<SystemTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setTools(await requestJson<SystemTool[]>('/api/tools/system'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load system tools');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { tools, loading, error, refresh };
}
