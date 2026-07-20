/** 当前 Project 资源 binding 读取 Hook。 */

import { useEffect, useMemo, useState } from 'react';
import type { ProjectResourceBinding, ProjectResourceType } from '../types/workspace';

export function useProjectResources(projectId: string) {
  const [bindings, setBindings] = useState<ProjectResourceBinding[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setBindings([]);
    if (!projectId) {
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
    void fetch(`/api/projects/${encodeURIComponent(projectId)}/resources`, {
      signal: controller.signal,
      cache: 'no-store',
    })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<ProjectResourceBinding[]>;
      })
      .then(setBindings)
      .catch((error) => {
        if (!(error instanceof DOMException && error.name === 'AbortError')) setBindings([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [projectId]);

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

  return { bindings, enabledByType, loading };
}
