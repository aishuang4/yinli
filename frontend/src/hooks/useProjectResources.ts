/** 当前 Project 资源 binding 读取 Hook。 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ProjectResourceBinding, ProjectResourceType } from '../types/workspace';

export function useProjectResources(projectId: string) {
  const [bindings, setBindings] = useState<ProjectResourceBinding[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    if (!projectId) {
      setBindings([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/resources`, {
        signal,
        cache: 'no-store',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setBindings(await response.json() as ProjectResourceBinding[]);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) setBindings([]);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    const controller = new AbortController();
    setBindings([]);
    void refresh(controller.signal);
    return () => controller.abort();
  }, [projectId, refresh]);

  const enabledByType = useMemo(() => {
    const result: Record<ProjectResourceType, Set<string>> = {
      agent: new Set(),
      skill: new Set(),
      mcp: new Set(),
      http_tool: new Set(),
    };
    bindings.forEach((binding) => {
      if (binding.enabled) result[binding.resourceType].add(binding.resourceId);
    });
    return result;
  }, [bindings]);

  return { bindings, enabledByType, loading, refresh: () => refresh() };
}
