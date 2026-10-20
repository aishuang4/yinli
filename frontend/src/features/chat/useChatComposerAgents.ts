/** Chat 可用 Agent 订阅与可见时刷新。 */
import { useEffect, useRef, type RefObject } from 'react';
import { usePageActive } from '../../hooks/usePageActive';
import { useAgentsStore } from '../../stores/agentsStore';
import { useAppStore } from '../../stores/appStore';
import type { ComposerAgentOption } from './ChatAgentSelect';

export function useChatComposerAgents(): {
  agents: ComposerAgentOption[];
  loading: boolean;
  hostRef: RefObject<HTMLDivElement | null>;
} {
  const pageActive = usePageActive();
  const currentView = useAppStore((state) => state.currentView);
  const agents = useAgentsStore((state) => state.agents);
  const loading = useAgentsStore((state) => state.loading);
  const hostRef = useRef<HTMLDivElement>(null);
  const options = agents
    .filter((agent) => agent.role !== 'job')
    .map((agent) => ({
      id: agent.id,
      name: agent.name,
      avatar: agent.avatar,
      description: agent.description,
    }));

  useEffect(() => {
    if (currentView !== 'chat' || !pageActive) {
      return;
    }
    void useAgentsStore.getState().syncFromServer({ silent: true });
  }, [currentView, pageActive]);

  useEffect(() => {
    const node = hostRef.current;
    if (!node) {
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) {
        return;
      }
      void useAgentsStore.getState().syncFromServer({ silent: true });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return { agents: options, loading, hostRef };
}

