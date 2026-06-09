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
  source_session_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface AgentMemory {
  agent_id: string;
  facts: MemoryFact[];
  events: MemoryEvent[];
  candidates: MemoryCandidate[];
  main_memory: string;
  main_memory_path: string;
  updated_at: string;
}
