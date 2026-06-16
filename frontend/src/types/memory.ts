export type MemoryEventType =
  | 'user_message'
  | 'assistant_message'
  | 'skill_use'
  | 'tool_use'
  | 'team_message'
  | 'note';

export interface MemoryFact {
  id: string;
  title: string;
  content: string;
  tags: string[];
  pinned: boolean;
  status: 'active' | 'outdated';
  access_count: number;
  last_accessed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryEvent {
  id: string;
  type: MemoryEventType;
  title: string;
  content: string;
  session_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface MemoryCandidate {
  id: string;
  agent_id: string;
  title: string;
  content: string;
  tags: string[];
  category: string;
  confidence: number;
  source_session_id: string | null;
  status: string;
  reject_reason: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryCleanupSuggestion {
  id: string;
  type: 'duplicate' | 'conflict' | 'stale';
  title: string;
  description: string;
  fact_ids: string[];
  keep_fact_id: string | null;
  confidence: number;
}

export interface AgentMemory {
  agent_id: string;
  facts: MemoryFact[];
  events: MemoryEvent[];
  candidates: MemoryCandidate[];
  cleanup_suggestions: MemoryCleanupSuggestion[];
  main_memory: string;
  main_memory_path: string;
  updated_at: string;
}
