import { useCallback, useEffect, useState } from 'react';
import type { AgentMemory } from '../types/memory';

interface UseAgentMemoryReturn {
  memory: AgentMemory | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useAgentMemory(agentId: string | null): UseAgentMemoryReturn {
  const [memory, setMemory] = useState<AgentMemory | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!agentId) {
      setMemory(null);
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const response = await fetch(`/api/agent-memory/${agentId}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setMemory(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load memory');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { memory, loading, error, refresh };
}
