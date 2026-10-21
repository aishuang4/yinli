/** 当前租户的 Project 目录。未选择时落到默认项目。 */

import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import type { ProjectRecord } from '../types/project';

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: 'no-store', ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function useProjects() {
  const activeProjectSlug = useAppStore((state) => state.activeProjectSlug);
  const setActiveProject = useAppStore((state) => state.setActiveProject);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const records = await requestJson<ProjectRecord[]>('/api/projects?includeArchived=true');
    setProjects(records);
    const active = records.filter((item) => item.status === 'active');
    const current = active.find((item) => item.slug === useAppStore.getState().activeProjectSlug);
    if (!current) {
      const fallback = active.find((item) => item.slug === 'default') || active[0];
      if (fallback) setActiveProject(fallback.slug);
    }
    return records;
  }, [setActiveProject]);

  useEffect(() => {
    let cancelled = false;
    async function initialize() {
      try {
        setLoading(true);
        setError(null);
        await refresh();
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Project 加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void initialize();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const selectProject = useCallback((slug: string) => {
    setActiveProject(slug || 'default');
  }, [setActiveProject]);

  const createProject = useCallback(async (payload: { name: string; primaryPath?: string }) => {
    const created = await requestJson<ProjectRecord>('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: payload.name,
        primaryPath: payload.primaryPath || undefined,
      }),
    });
    await refresh();
    setActiveProject(created.slug);
    return created;
  }, [refresh, setActiveProject]);

  const archiveProject = useCallback(async (slug: string) => {
    await requestJson<ProjectRecord>(`/api/projects/${encodeURIComponent(slug)}/archive`, { method: 'POST' });
    if (useAppStore.getState().activeProjectSlug === slug) setActiveProject('default');
    await refresh();
  }, [refresh, setActiveProject]);

  const addFolder = useCallback(async (slug: string, path: string, accessMode: 'read' | 'readwrite') => {
    const updated = await requestJson<ProjectRecord>(`/api/projects/${encodeURIComponent(slug)}/folders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, accessMode }),
    });
    setProjects((current) => current.map((item) => (item.slug === updated.slug ? updated : item)));
    return updated;
  }, []);

  const removeFolder = useCallback(async (slug: string, path: string) => {
    const updated = await requestJson<ProjectRecord>(`/api/projects/${encodeURIComponent(slug)}/folders`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    setProjects((current) => current.map((item) => (item.slug === updated.slug ? updated : item)));
    return updated;
  }, []);

  const activeProject = projects.find((item) => item.slug === activeProjectSlug) || null;

  return {
    projects,
    activeProjects: projects.filter((item) => item.status === 'active'),
    activeProject,
    activeProjectSlug,
    loading,
    error,
    selectProject,
    createProject,
    archiveProject,
    addFolder,
    removeFolder,
    refresh,
  };
}
