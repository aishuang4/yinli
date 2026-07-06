/** Memory 页面相关类型。 */

export type MarkdownMemoryScope = 'user' | 'global' | 'agent';

export interface MarkdownMemoryDocument {
  scope: string;
  ownerId: string;
  path: string;
  content: string;
  exists: boolean;
}

export interface MemoryNodeTreeItem {
  nodeId: string;
  date: string;
  agentId: string;
  title: string;
  summary: string;
  tags: string[];
}

export interface MemoryNodeQueryResult {
  nodeId: string;
  date: string;
  title?: string;
  dateRangeStart?: string;
  dateRangeEnd?: string;
  agentId: string;
  summary: string;
  conversationDigest: string;
  userInputs: string[];
  agentFinalOutputs: string[];
  tags: string[];
  confidence?: number;
  sourceSessionId?: string;
  sourceRunId?: string;
  sourceRunIds?: string[];
  sourceMessageId?: string;
  lastUsedAt?: string;
  useCount?: number;
}
