/** Workspace / Project 前端上下文类型。 */

export interface WorkspaceRecord {
  id: string;
  name: string;
  description: string;
  status: string;
  createdAt: string;
  updatedAt: string;
}

export interface ProjectRecord {
  id: string;
  workspaceId: string;
  name: string;
  description: string;
  rootPath: string;
  instructionsFile: string;
  status: string;
  createdAt: string;
  updatedAt: string;
}

export type ProjectResourceType = 'agent' | 'skill' | 'mcp' | 'http_tool';

export interface ProjectResourceBinding {
  projectId: string;
  resourceType: ProjectResourceType;
  resourceId: string;
  enabled: boolean;
  configOverride: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}
