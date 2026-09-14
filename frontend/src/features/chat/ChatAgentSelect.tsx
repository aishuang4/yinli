/** Chat Agent 下拉：直接订阅全局 agentsStore；页面重新可见时再拉一次列表。 */

import { useEffect, useRef } from 'react';
import { Select } from '@/components/design';
import { usePageActive } from '../../hooks/usePageActive';
import { useAgentsStore } from '../../stores/agentsStore';
import { useAppStore } from '../../stores/appStore';

interface ChatAgentSelectProps {
  selectedAgentId: string;
  disabled?: boolean;
  onChange: (id: string) => void;
}

export function ChatAgentSelect({ selectedAgentId, disabled, onChange }: ChatAgentSelectProps) {
  const pageActive = usePageActive();
  const currentView = useAppStore((state) => state.currentView);
  const workspaceId = useAppStore((state) => state.activeWorkspaceId);
  const agents = useAgentsStore((state) => state.agents);
  const revision = useAgentsStore((state) => state.revision);
  const loading = useAgentsStore((state) => state.loading);
  const hostRef = useRef<HTMLDivElement>(null);
  const options = agents
    .filter((agent) => (
      agent.role !== 'job'
      && (agent.id === 'default' || agent.workspaceId === workspaceId)
    ))
    .map((agent) => ({
      label: agent.id === 'default' ? `${agent.name} - 默认助手` : agent.name,
      value: agent.id,
    }));
  const selectedLabel = options.find((item) => item.value === selectedAgentId)?.label ?? '';

  useEffect(() => {
    if (currentView !== 'chat') {
      return;
    }
    void useAgentsStore.getState().syncFromServer(workspaceId, { silent: true });
  }, [currentView, workspaceId]);

  useEffect(() => {
    const node = hostRef.current;
    if (!node) {
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) {
        return;
      }
      void useAgentsStore.getState().syncFromServer(workspaceId, { silent: true });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [workspaceId]);

  return (
    <div ref={hostRef}>
      <Select
        key={`${pageActive ? 'on' : 'off'}:${currentView}:${revision}:${selectedAgentId}:${selectedLabel}`}
        value={selectedAgentId}
        onChange={onChange}
        options={options}
        loading={loading}
        disabled={disabled}
        placeholder="选择 Agent"
        style={{ width: 160 }}
      />
    </div>
  );
}
