/** Chat 发送框当前 Agent：订阅 agentsStore，并提供头像与工具栏 chip。 */

import { useEffect, useRef, type RefObject } from 'react';
import { Bot } from 'lucide-react';
import { usePageActive } from '../../hooks/usePageActive';
import { useAgentsStore } from '../../stores/agentsStore';
import { useAppStore } from '../../stores/appStore';
import type { Agent } from '../../types/agent';

export type ComposerAgentOption = Pick<Agent, 'id' | 'name' | 'avatar' | 'description'>;

export function useChatComposerAgents(): {
  agents: ComposerAgentOption[];
  loading: boolean;
  hostRef: RefObject<HTMLDivElement | null>;
} {
  const pageActive = usePageActive();
  const currentView = useAppStore((state) => state.currentView);
  const workspaceId = useAppStore((state) => state.activeWorkspaceId);
  const agents = useAgentsStore((state) => state.agents);
  const loading = useAgentsStore((state) => state.loading);
  const hostRef = useRef<HTMLDivElement>(null);
  const options = agents
    .filter((agent) => (
      agent.role !== 'job'
      && (agent.id === 'default' || agent.workspaceId === workspaceId)
    ))
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
    void useAgentsStore.getState().syncFromServer(workspaceId, { silent: true });
  }, [currentView, pageActive, workspaceId]);

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

  return { agents: options, loading, hostRef };
}

export function ChatAgentAvatar({
  agent,
  size = 20,
}: {
  agent: ComposerAgentOption | null;
  size?: number;
}) {
  const avatar = agent?.avatar?.trim() ?? '';
  const isImage = /^(https?:|data:|blob:|\/)/.test(avatar) || avatar.includes('/');
  if (avatar && isImage) {
    return (
      <img
        src={avatar}
        alt=""
        className="shrink-0 rounded-full object-cover"
        style={{ width: size, height: size }}
      />
    );
  }
  if (avatar) {
    return (
      <span
        className="inline-flex shrink-0 items-center justify-center rounded-full bg-[var(--color-bg-hover)] text-[11px] leading-none"
        style={{ width: size, height: size }}
        aria-hidden="true"
      >
        {avatar.slice(0, 2)}
      </span>
    );
  }
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]"
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <Bot size={Math.max(12, Math.round(size * 0.6))} />
    </span>
  );
}

export function ChatAgentChip({
  agent,
  teamMode,
  disabled,
  onClick,
}: {
  agent: ComposerAgentOption | null;
  teamMode?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  const label = agent?.name || '选择 Agent';
  return (
    <button
      type="button"
      className="inline-flex h-8 max-w-[168px] items-center gap-1.5 rounded-[10px] px-2 text-sm leading-none text-[var(--color-text-primary)] transition-colors duration-200 hover:bg-[var(--color-bg-hover)] disabled:cursor-not-allowed disabled:opacity-40"
      onClick={onClick}
      disabled={disabled}
      aria-label={teamMode ? `当前 Team Leader ${label}` : `当前 Agent ${label}`}
    >
      <ChatAgentAvatar agent={agent} size={20} />
      <span className="min-w-0 truncate leading-none">{label}</span>
      {teamMode ? (
        <span className="shrink-0 text-[10px] text-[var(--color-text-tertiary)]">Team</span>
      ) : null}
    </button>
  );
}
