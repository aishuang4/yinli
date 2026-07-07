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

export type SkillEvolutionRecordStatus = 'draft' | 'registered' | 'skipped' | 'failed';

export interface SkillEvolutionRecord {
  id: string;
  workspaceId: string;
  projectId: string;
  agentId: string;
  agentName: string;
  teamId: string;
  userId: string;
  sourceSessionId: string;
  sourceRunId: string;
  sourceMessageId: string;
  routerGate: {
    shouldRun?: boolean;
    reason?: string;
    risk?: string;
    signals?: string[];
  };
  workflowEvidence: {
    goal?: string;
    finalOutput?: string;
    successfulToolCalls?: Array<Record<string, unknown>>;
    valuableFailures?: Array<Record<string, unknown>>;
    changedFiles?: string[];
    validationSignals?: string[];
    variableFlow?: Array<Record<string, unknown>>;
  };
  reusable: boolean;
  reusableReason: string;
  triggerDescription: string;
  inputsSchema: Record<string, unknown>;
  draftSkillName: string;
  draftSkillDescription: string;
  draftSkillContent: string;
  draftTags: string[];
  granularity: string;
  requiresWorkflow: boolean;
  suggestedSplit: string[];
  variableFlow: Array<Record<string, unknown>>;
  riskNotes: string;
  confidence: number;
  status: SkillEvolutionRecordStatus;
  registeredSkillId: string;
  createdAt: string;
  updatedAt: string;
}

export interface MemoryWikiSyncResult {
  path: string;
  filesWritten: number;
  markdownMemories: number;
  historyNodes: number;
  skills: number;
  entities: number;
  files: string[];
}
