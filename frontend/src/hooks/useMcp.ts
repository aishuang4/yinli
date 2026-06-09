/**
 * useMcp — MCP Server 浏览和安装管理 Hook
 *
 * 封装 /api/mcp 相关调用，供 MCP Browser 和 AgentEditor 复用。
 */

import { useCallback, useEffect, useState } from 'react';
import type { McpRegisterPayload, McpServer, McpTool } from '../types/mcp';

interface UseMcpReturn {
  servers: McpServer[];
  categories: string[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  registerServer: (payload: McpRegisterPayload) => Promise<McpServer>;
  installServer: (id: string) => Promise<McpServer>;
  uninstallServer: (id: string) => Promise<McpServer>;
  setServerEnabled: (id: string, enabled: boolean) => Promise<McpServer>;
}

interface UseInstalledMcpToolsReturn {
  tools: McpTool[];
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

export function useMcp(): UseMcpReturn {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [serverList, categoryList] = await Promise.all([
        requestJson<McpServer[]>('/api/mcp'),
        requestJson<string[]>('/api/mcp/categories'),
      ]);
      setServers(serverList);
      setCategories(categoryList);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load MCP servers');
    } finally {
      setLoading(false);
    }
  }, []);

  const updateServer = useCallback((server: McpServer) => {
    setServers((prev) => {
      if (prev.some((item) => item.id === server.id)) {
        return prev.map((item) => (item.id === server.id ? server : item));
      }
      return [...prev, server];
    });
  }, []);

  const registerServer = useCallback(async (payload: McpRegisterPayload) => {
    const server = await requestJson<McpServer>('/api/mcp/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    updateServer(server);
    setCategories((prev) => (
      prev.includes(server.category) ? prev : [...prev, server.category].sort()
    ));
    return server;
  }, [updateServer]);

  const installServer = useCallback(async (id: string) => {
    const server = await requestJson<McpServer>(`/api/mcp/${id}/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: true }),
    });
    updateServer(server);
    return server;
  }, [updateServer]);

  const uninstallServer = useCallback(async (id: string) => {
    const server = await requestJson<McpServer>(`/api/mcp/${id}/uninstall`, {
      method: 'POST',
    });
    updateServer(server);
    return server;
  }, [updateServer]);

  const setServerEnabled = useCallback(async (id: string, enabled: boolean) => {
    const server = await requestJson<McpServer>(`/api/mcp/${id}/enable`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    updateServer(server);
    return server;
  }, [updateServer]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    servers,
    categories,
    loading,
    error,
    refresh,
    registerServer,
    installServer,
    uninstallServer,
    setServerEnabled,
  };
}

export function useInstalledMcpTools(): UseInstalledMcpToolsReturn {
  const [tools, setTools] = useState<McpTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setTools(await requestJson<McpTool[]>('/api/mcp/tools'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load MCP tools');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { tools, loading, error, refresh };
}
