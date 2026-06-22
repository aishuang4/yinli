/** Configurable local tool definitions. */

export interface KnowledgeTool {
  id: string;
  name: string;
  description: string;
  url: string;
  authorization: string;
  headers: Record<string, string>;
  defaultPayload: Record<string, unknown>;
  parameterInstructions: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export type KnowledgeToolPayload = Omit<KnowledgeTool, 'id' | 'created_at' | 'updated_at'>;

export type HttpToolMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface HttpTool {
  id: string;
  name: string;
  description: string;
  category: string;
  method: HttpToolMethod;
  url: string;
  authType: string;
  authConfig: Record<string, unknown>;
  headers: Record<string, string>;
  queryTemplate: Record<string, unknown>;
  bodyTemplate: Record<string, unknown>;
  parameterSchema: Record<string, unknown>;
  parameterInstructions: string;
  responseMode: string;
  responsePath: string;
  readonly: boolean;
  approvalPolicy: string;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export type HttpToolPayload = Omit<HttpTool, 'id' | 'createdAt' | 'updatedAt'>;

export interface SystemTool {
  name: string;
  description: string;
  category: string;
  action: string;
  risk: string;
  readonly: boolean;
  defaultEnabled: boolean;
}
