/** Project 资源绑定已删除。Skill / MCP / HTTP 按租户目录可见，这里只保留空集合给旧调用点。 */

import { useMemo } from 'react';

export function useProjectResources(_projectSlug: string) {
  const enabledByType = useMemo(() => ({
    agent: new Set<string>(),
    skill: new Set<string>(),
    mcp: new Set<string>(),
    http_tool: new Set<string>(),
  }), []);
  return { bindings: [], enabledByType, loading: false, refresh: async () => undefined };
}
