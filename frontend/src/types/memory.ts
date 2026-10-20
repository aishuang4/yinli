/** Memory 页面相关类型。 */

export type MarkdownMemoryScope = 'user' | 'global' | 'agent';
export type MemoryNodeStatus = 'active' | 'disabled' | 'archived' | 'deleted';

export interface MarkdownMemoryDocument {
  scope: string;
  ownerId: string;
  path: string;
  content: string;
  exists: boolean;
}

export interface TimelineMessage {
  id: string;
  sessionId: string;
  groupId: string;
  role: string;
  content: string;
  agentId: string;
  agentName: string;
  createdAt: string;
  surface: string;
  workspaceId: string;
  projectId: string;
}

export interface TimelineQuery {
  sessionId?: string;
  agentId?: string;
  surface?: string;
  timeFrom?: string;
  timeTo?: string;
  queryText?: string;
}

export interface TimelineQueryResult {
  messages: TimelineMessage[];
  truncated: boolean;
  hitCount: number;
}

export interface MemoryNodeTreeItem {
  nodeId: string;
  date: string;
  agentId: string;
  status: MemoryNodeStatus;
  title: string;
  summary: string;
  tags: string[];
}

export interface MemoryNodeQueryResult {
  nodeId: string;
  date: string;
  status?: MemoryNodeStatus;
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

export type MemoryCandidateStatus = 'pending' | 'accepted' | 'rejected' | 'failed';
export type MemoryCandidateAction = 'accept' | 'reject';

/** user / global 层待用户确认的 Markdown 记忆候选；采纳后才真正写入文件。 */
export interface MemoryMarkdownCandidate {
  id: string;
  scope: 'user' | 'agent' | 'global_preference';
  ownerId: string;
  section: string;
  operation: 'append' | 'update' | 'delete';
  note: string;
  exactText: string;
  replacementText: string;
  reason: string;
  confidence: number;
  status: MemoryCandidateStatus;
  resolution: string;
  agentId: string;
  agentName: string;
  workspaceId: string;
  projectId: string;
  sourceSessionId: string;
  sourceRunId: string;
  createdAt: string;
  updatedAt: string;
}

export type SkillEvolutionRecordStatus = 'draft' | 'registered' | 'skipped' | 'failed';
export type SkillEvolutionRecordAction = 'skip' | 'restore_draft' | 'mark_failed' | 'register';

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

export interface MemoryAuditEvent {
  id: string;
  sessionId: string;
  eventType: string;
  scope: string;
  agentId?: string;
  agentName?: string;
  status: string;
  title: string;
  content: string;
  payload: Record<string, unknown>;
  createdAt: string;
  sortOrder: number;
}

export type MemoryRetrievalFeedbackRating = 'relevant' | 'irrelevant';

export interface MemoryRetrievalFeedbackPayload {
  auditEventId: string;
  nodeId: string;
  rating: MemoryRetrievalFeedbackRating;
  reason?: string;
  sessionId?: string;
  agentId?: string;
  query?: string;
}

export interface MemorySettingsSnapshot {
  vectorStore: {
    provider: string;
    embeddingProvider: string;
    embeddingModel: string;
    embeddingDimensions: number | null;
    retrievalLevel: number;
    schemaVersion: number;
  };
  admission: {
    historyThreshold: number;
    markdownThreshold: number;
    skillThreshold: number;
    minUserChars: number;
    minAssistantChars: number;
    rejectSensitive: boolean;
    enableRedaction: boolean;
    enableModelScoring: boolean;
    modelScoreWeight: number;
    modelTimeoutSeconds: number;
    summaryMaxItemsPerSection: number;
    summaryMaxChars: number;
    markdownWriteRetries: number;
    confirmUserMarkdown: boolean;
    confirmGlobalMarkdown: boolean;
    mode: string;
  };
  markdownPaths: Record<string, string>;
  runtime: {
    memoryRuntimeKind: boolean;
    usesHooksAndTools: boolean;
    chatShowsMemoryProcess: boolean;
  };
}

export type MemoryAdmissionPolicyUpdate = Partial<MemorySettingsSnapshot['admission']>;
