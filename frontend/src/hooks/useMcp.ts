/**
 * useMcp — MCP Server 浏览和安装管理 Hook
 *
 * 封装 /api/mcp 相关调用，供 MCP Browser 和 AgentEditor 复用。
 */

import { useCallback, useEffect, useState } from 'react';
import type { McpRegisterPayload, McpServer, McpTool } from '../types/mcp';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';
import { useAppStore } from '../stores/appStore';
import { bindProjectResource } from '../utils/projectResources';

interface UseMcpReturn {
  servers: McpServer[];
  categories: string[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  registerServer: (payload: McpRegisterPayload) => Promise<McpServer>;
  installServer: (id: string) => Promise<McpServer>;
  uninstallServer: (id: string) => Promise<McpServer>;
  deleteServer: (id: string) => Promise<void>;
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
  const activeWorkspaceId = useAppStore((state) => state.activeWorkspaceId);
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const [servers, setServers] = useState<McpServer[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [serverList, categoryList] = await Promise.all([
        requestJson<McpServer[]>(`/api/mcp?workspaceId=${encodeURIComponent(activeWorkspaceId)}`),
        requestJson<string[]>('/api/mcp/categories'),
      ]);
      setServers(serverList);
      setCategories(categoryList);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load MCP servers');
    } finally {
      setLoading(false);
    }
  }, [activeWorkspaceId]);

  const updateServer = useCallback((server: McpServer) => {
    setServers((prev) => {
      if (prev.some((item) => item.id === server.id)) {
        return prev.map((item) => (item.id === server.id ? server : item));
      }
      return [...prev, server];
    });
  }, []);

  const registerServer = useCallback(async (payload: McpRegisterPayload) => {
    try {
      const server = await requestJson<McpServer>('/api/mcp/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, workspaceId: activeWorkspaceId }),
      });
      await bindProjectResource(activeProjectId, 'mcp', server.id);
      updateServer(server);
      setCategories((prev) => (
        prev.includes(server.category) ? prev : [...prev, server.category].sort()
      ));
      notifySuccess('MCP 注册成功');
      return server;
    } catch (err) {
      notifyError(`MCP 注册失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [activeProjectId, activeWorkspaceId, updateServer]);

  const installServer = useCallback(async (id: string) => {
    try {
      const server = await requestJson<McpServer>(`/api/mcp/${id}/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true }),
      });
      updateServer(server);
      notifySuccess('MCP 安装成功');
      return server;
    } catch (err) {
      notifyError(`MCP 安装失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [updateServer]);

  const uninstallServer = useCallback(async (id: string) => {
    try {
      const server = await requestJson<McpServer>(`/api/mcp/${id}/uninstall`, {
        method: 'POST',
      });
      updateServer(server);
      notifySuccess('MCP 卸载成功');
      return server;
    } catch (err) {
      notifyError(`MCP 卸载失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [updateServer]);

  const deleteServer = useCallback(async (id: string) => {
    try {
      const response = await fetch(`/api/mcp/${id}`, { method: 'DELETE' });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }
      setServers((prev) => prev.filter((server) => server.id !== id));
      notifySuccess('MCP 删除成功');
    } catch (err) {
      notifyError(`MCP 删除失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, []);

  const setServerEnabled = useCallback(async (id: string, enabled: boolean) => {
    try {
      const server = await requestJson<McpServer>(`/api/mcp/${id}/enable`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      updateServer(server);
      notifySuccess(enabled ? 'MCP 已启用' : 'MCP 已停用');
      return server;
    } catch (err) {
      notifyError(`MCP 状态更新失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
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
    deleteServer,
    setServerEnabled,
  };
}

export function useInstalledMcpTools(): UseInstalledMcpToolsReturn {
  const activeWorkspaceId = useAppStore((state) => state.activeWorkspaceId);
  const [tools, setTools] = useState<McpTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setTools(await requestJson<McpTool[]>(
        `/api/mcp/tools?workspaceId=${encodeURIComponent(activeWorkspaceId)}`,
      ));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load MCP tools');
    } finally {
      setLoading(false);
    }
  }, [activeWorkspaceId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { tools, loading, error, refresh };
}
