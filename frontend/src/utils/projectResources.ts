/** Project 资源 binding 写入辅助。 */

import type { ProjectResourceType } from '../types/workspace';

export async function bindProjectResource(
  projectId: string,
  resourceType: ProjectResourceType,
  resourceId: string,
): Promise<void> {
  const response = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/resources/${resourceType}/${encodeURIComponent(resourceId)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: true }),
    },
  );
  if (!response.ok) throw new Error(`Project 资源绑定失败：HTTP ${response.status}`);
}
