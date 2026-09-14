/**
 * Agent 列表的全局状态。
 *
 * Chat 是 keep-alive（display:none 不卸载），不能各自一份列表。
 * Agents 页保存后必须写这里；Chat 下拉必须读这里。
 */

import { create } from 'zustand';
import type { Agent } from '../types/agent';

interface AgentApiData {
  id: string;
  systemManaged?: boolean;
  workspaceId?: string;
  name: string;
  avatar: string;
  description: string;
  system_prompt: string;
  modelConfig: {
    max_react_iters?: number;
    maxReactIters?: number;
  };
  mcpTools: string[];
  knowledgeTools?: string[];
  skills: string[];
  tags: string[];
  planConfig?: Agent['planConfig'];
  guardrails: {
    filesystem_scope: string;
    shell_blacklist: string[];
    network_enabled: boolean;
    human_approval: boolean;
    tool_permission_mode?: Agent['guardrails']['toolPermissionMode'];
    toolPermissionMode?: Agent['guardrails']['toolPermissionMode'];
    tool_permission_profile?: Agent['guardrails']['toolPermissionProfile'];
    toolPermissionProfile?: Agent['guardrails']['toolPermissionProfile'];
    execution_backend?: Agent['guardrails']['executionBackend'];
    executionBackend?: Agent['guardrails']['executionBackend'];
    docker_image?: string;
    dockerImage?: string;
    persistent_sandbox?: boolean;
    persistentSandbox?: boolean;
    timeout_seconds: number;
  };
  status: string;
  created_at: string;
  updated_at: string;
  role?: 'chat' | 'job';
  jobToolkit?: Agent['jobToolkit'];
}

function profileFromLegacyToolMode(
  mode: string | undefined,
): Agent['guardrails']['toolPermissionProfile'] {
  if (mode === 'ask_every_time') return 'ask_every_time';
  if (mode === 'bypass') return 'trusted_tools';
  return 'confirm_destructive';
}

export function mapApiAgent(api: AgentApiData): Agent {
  return {
    id: api.id,
    workspaceId: api.workspaceId ?? 'default',
    name: api.name,
    avatar: api.avatar,
    description: api.description,
    systemPrompt: api.system_prompt,
    modelConfig: {
      maxReactIters: api.modelConfig.max_react_iters ?? api.modelConfig.maxReactIters ?? 40,
    },
    mcpTools: api.mcpTools,
    knowledgeTools: api.knowledgeTools ?? [],
    skills: api.skills,
    tags: api.tags,
    planConfig: {
      enabled: api.planConfig?.enabled ?? false,
      mode: api.planConfig?.mode ?? 'structured',
      maxSteps: api.planConfig?.maxSteps ?? 6,
      requirePlanBeforeTools: api.planConfig?.requirePlanBeforeTools ?? false,
      requireUserApproval: api.planConfig?.requireUserApproval ?? false,
      allowReplanning: api.planConfig?.allowReplanning ?? false,
    },
    guardrails: {
      filesystemScope: api.guardrails.filesystem_scope,
      shellBlacklist: api.guardrails.shell_blacklist,
      networkEnabled: api.guardrails.network_enabled,
      humanApproval: api.guardrails.human_approval,
      toolPermissionMode: api.guardrails.tool_permission_mode
        ?? api.guardrails.toolPermissionMode
        ?? 'accept_edits',
      toolPermissionProfile: api.guardrails.tool_permission_profile
        ?? api.guardrails.toolPermissionProfile
        ?? profileFromLegacyToolMode(api.guardrails.tool_permission_mode ?? api.guardrails.toolPermissionMode),
      executionBackend: api.guardrails.execution_backend ?? api.guardrails.executionBackend ?? 'local',
      dockerImage: api.guardrails.docker_image ?? api.guardrails.dockerImage ?? 'python:3.11-slim',
      persistentSandbox: api.guardrails.persistent_sandbox ?? api.guardrails.persistentSandbox ?? false,
      timeoutSeconds: api.guardrails.timeout_seconds,
    },
    status: api.status as Agent['status'],
    createdAt: api.created_at,
    updatedAt: api.updated_at,
    role: api.role === 'job' ? 'job' : 'chat',
    jobToolkit: {
      required: (api.jobToolkit?.required ?? []).map((item) => ({
        kind: item.kind as Agent['jobToolkit']['required'][number]['kind'],
        id: item.id,
      })),
      optional: (api.jobToolkit?.optional ?? []).map((item) => ({
        kind: item.kind as Agent['jobToolkit']['optional'][number]['kind'],
        id: item.id,
      })),
    },
    systemManaged: api.systemManaged === true,
  };
}

interface AgentsStore {
  agents: Agent[];
  loading: boolean;
  error: string | null;
  revision: number;
  syncFromServer: (workspaceId: string, options?: { silent?: boolean }) => Promise<void>;
  upsertAgent: (agent: Agent) => void;
  removeAgent: (id: string) => void;
}

export const useAgentsStore = create<AgentsStore>()((set, get) => ({
  agents: [],
  loading: false,
  error: null,
  revision: 0,
  syncFromServer: async (workspaceId, options) => {
    const silent = Boolean(options?.silent) && get().agents.length > 0;
    try {
      if (!silent) set({ loading: true, error: null });
      else set({ error: null });
      const res = await fetch(`/api/agents?workspaceId=${encodeURIComponent(workspaceId)}`, {
        cache: 'no-store',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: AgentApiData[] = await res.json();
      const agents = data.map(mapApiAgent).filter((agent) => (
        agent.systemManaged || agent.workspaceId === workspaceId
      ));
      set((state) => ({
        agents,
        loading: false,
        revision: state.revision + 1,
      }));
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : 'Failed to load agents',
        loading: silent ? get().loading : false,
      });
    }
  },
  upsertAgent: (agent) => set((state) => {
    const exists = state.agents.some((item) => item.id === agent.id);
    return {
      revision: state.revision + 1,
      agents: exists
        ? state.agents.map((item) => (item.id === agent.id ? agent : item))
        : [...state.agents, agent],
    };
  }),
  removeAgent: (id) => set((state) => ({
    revision: state.revision + 1,
    agents: state.agents.filter((item) => item.id !== id),
  })),
}));
