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
