/** Workspace / Project 全局上下文目录。 */

import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import type { ProjectRecord, WorkspaceRecord } from '../types/workspace';

async function requestJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export function useWorkspaceProjects() {
  const activeWorkspaceId = useAppStore((state) => state.activeWorkspaceId);
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const setActiveContext = useAppStore((state) => state.setActiveContext);
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [allProjects, setAllProjects] = useState<ProjectRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadProjects = useCallback(async (workspaceId: string) => (
    requestJson<ProjectRecord[]>(`/api/workspaces/${encodeURIComponent(workspaceId)}/projects`)
  ), []);

  useEffect(() => {
    let cancelled = false;
    async function initialize() {
      try {
        setLoading(true);
        setError(null);
        const workspaceRecords = await requestJson<WorkspaceRecord[]>('/api/workspaces');
        if (cancelled) return;
        setWorkspaces(workspaceRecords);
        const projectCatalog = (await Promise.all(
          workspaceRecords.map((item) => loadProjects(item.id)),
        )).flat();
        if (cancelled) return;
        setAllProjects(projectCatalog);
        const workspace = workspaceRecords.find((item) => item.id === activeWorkspaceId) || workspaceRecords[0];
        if (!workspace) {
          setProjects([]);
          return;
        }
        const projectRecords = projectCatalog.filter((item) => item.workspaceId === workspace.id);
        setProjects(projectRecords);
        const project = projectRecords.find((item) => item.id === activeProjectId) || projectRecords[0];
        if (project && (workspace.id !== activeWorkspaceId || project.id !== activeProjectId)) {
          setActiveContext(workspace.id, project.id);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : '上下文加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void initialize();
    return () => {
      cancelled = true;
    };
  }, [activeProjectId, activeWorkspaceId, loadProjects, setActiveContext]);

  const selectWorkspace = useCallback(async (workspaceId: string) => {
    try {
      setLoading(true);
      setError(null);
      const projectRecords = await loadProjects(workspaceId);
      setProjects(projectRecords);
      setActiveContext(workspaceId, projectRecords[0]?.id || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Project 加载失败');
    } finally {
      setLoading(false);
    }
  }, [loadProjects, setActiveContext]);

  const selectProject = useCallback((projectId: string) => {
    const project = projects.find((item) => item.id === projectId);
    if (project) setActiveContext(project.workspaceId, project.id);
  }, [projects, setActiveContext]);

  return {
    workspaces,
    projects,
    allProjects,
    activeWorkspaceId,
    activeProjectId,
    loading,
    error,
    selectWorkspace,
    selectProject,
  };
}
