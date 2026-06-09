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

export interface AgentMemory {
  agent_id: string;
  facts: MemoryFact[];
  events: MemoryEvent[];
  updated_at: string;
}
