/**
 * useAgents — Agent CRUD 操作 Hook
 *
 * 封装 Agent 相关的 API 调用和状态管理。
 * 使用 SWR 风格的缓存策略（MVP 阶段手动实现）。
 *
 * @see 后端 API: GET/POST/PUT/DELETE /api/agents
 */

import { useState, useCallback, useEffect } from 'react';
import type {
  Agent,
  AgentCreatePayload,
  AgentUpdatePayload,
  ModelsResponse,
} from '../types/agent';

/** Hook 返回值 */
interface UseAgentsReturn {
  /** Agent 列表 */
  agents: Agent[];
  /** 是否正在加载 */
  loading: boolean;
  /** 错误信息 */
  error: string | null;
  /** 可用模型列表 */
  models: ModelsResponse | null;

  /** 刷新 Agent 列表 */
  refresh: () => Promise<void>;
  /** 创建 Agent */
  createAgent: (data: AgentCreatePayload) => Promise<Agent>;
  /** 更新 Agent */
  updateAgent: (id: string, data: AgentUpdatePayload) => Promise<Agent>;
  /** 删除 Agent */
  deleteAgent: (id: string) => Promise<void>;
  /** 获取单个 Agent */
  getAgent: (id: string) => Promise<Agent>;
}

/** API 响应中的 Agent（snake_case） */
interface AgentApiData {
  id: string;
  name: string;
  avatar: string;
  description: string;
  system_prompt: string;
  modelConfig: {
    primary: string;
    fallback: string[];
    temperature: number;
    max_tokens: number;
  };
  mcpTools: string[];
  skills: string[];
  tags: string[];
  callableAgents: string[];
  maxDepth: number;
  guardrails: {
    filesystem_scope: string;
    shell_blacklist: string[];
    network_enabled: boolean;
    human_approval: boolean;
    timeout_seconds: number;
  };
  status: string;
  created_at: string;
  updated_at: string;
}

/**
 * 将 API 响应的 snake_case 转换为前端 camelCase
 */
function mapApiAgent(api: AgentApiData): Agent {
  return {
    id: api.id,
    name: api.name,
    avatar: api.avatar,
    description: api.description,
    systemPrompt: api.system_prompt,
    modelConfig: {
      primary: api.modelConfig.primary,
      fallback: api.modelConfig.fallback,
      temperature: api.modelConfig.temperature,
      maxTokens: api.modelConfig.max_tokens,
    },
    mcpTools: api.mcpTools,
    skills: api.skills,
    tags: api.tags,
    callableAgents: api.callableAgents,
    maxDepth: api.maxDepth,
    guardrails: {
      filesystemScope: api.guardrails.filesystem_scope,
      shellBlacklist: api.guardrails.shell_blacklist,
      networkEnabled: api.guardrails.network_enabled,
      humanApproval: api.guardrails.human_approval,
      timeoutSeconds: api.guardrails.timeout_seconds,
    },
    status: api.status as Agent['status'],
    createdAt: api.created_at,
    updatedAt: api.updated_at,
  };
}

/**
 * 将前端 camelCase 转换为 API 请求的 snake_case
 */
function mapToApiPayload(data: AgentCreatePayload | AgentUpdatePayload): Record<string, unknown> {
  const result: Record<string, unknown> = {};

  if ('name' in data && data.name !== undefined) result.name = data.name;
  if ('avatar' in data && data.avatar !== undefined) result.avatar = data.avatar;
  if ('description' in data && data.description !== undefined) result.description = data.description;
  if ('systemPrompt' in data && data.systemPrompt !== undefined) result.system_prompt = data.systemPrompt;
  if ('tags' in data && data.tags !== undefined) result.tags = data.tags;
  if ('skills' in data && data.skills !== undefined) result.skills = data.skills;
  if ('mcpTools' in data && data.mcpTools !== undefined) result.mcpTools = data.mcpTools;
  if ('callableAgents' in data && data.callableAgents !== undefined) result.callableAgents = data.callableAgents;
  if ('maxDepth' in data && data.maxDepth !== undefined) result.maxDepth = data.maxDepth;

  if ('modelConfig' in data && data.modelConfig) {
    result.modelConfig = {
      primary: data.modelConfig.primary,
      fallback: data.modelConfig.fallback,
      temperature: data.modelConfig.temperature,
      max_tokens: data.modelConfig.maxTokens,
    };
  }

  if ('guardrails' in data && data.guardrails) {
    result.guardrails = {
      filesystem_scope: data.guardrails.filesystemScope,
      shell_blacklist: data.guardrails.shellBlacklist,
      network_enabled: data.guardrails.networkEnabled,
      human_approval: data.guardrails.humanApproval,
      timeout_seconds: data.guardrails.timeoutSeconds,
    };
  }

  return result;
}

/**
 * useAgents Hook
 */
export function useAgents(): UseAgentsReturn {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /** 获取 Agent 列表 */
  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetch('/api/agents');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: AgentApiData[] = await res.json();
      setAgents(data.map(mapApiAgent));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load agents');
    } finally {
      setLoading(false);
    }
  }, []);

  /** 获取可用模型列表 */
  const loadModels = useCallback(async () => {
    try {
      const res = await fetch('/api/agents/models');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setModels(data);
    } catch {
      // 模型列表加载失败不阻塞 UI
      console.warn('Failed to load models list');
    }
  }, []);

  /** 创建 Agent */
  const createAgent = useCallback(async (payload: AgentCreatePayload): Promise<Agent> => {
    const res = await fetch('/api/agents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(mapToApiPayload(payload)),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data: AgentApiData = await res.json();
    const agent = mapApiAgent(data);
    setAgents((prev) => [...prev, agent]);
    return agent;
  }, []);

  /** 更新 Agent */
  const updateAgent = useCallback(async (id: string, payload: AgentUpdatePayload): Promise<Agent> => {
    const res = await fetch(`/api/agents/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(mapToApiPayload(payload)),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data: AgentApiData = await res.json();
    const agent = mapApiAgent(data);
    setAgents((prev) => prev.map((a) => (a.id === id ? agent : a)));
    return agent;
  }, []);

  /** 删除 Agent */
  const deleteAgent = useCallback(async (id: string): Promise<void> => {
    const res = await fetch(`/api/agents/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    setAgents((prev) => prev.filter((a) => a.id !== id));
  }, []);

  /** 获取单个 Agent */
  const getAgent = useCallback(async (id: string): Promise<Agent> => {
    const res = await fetch(`/api/agents/${id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data: AgentApiData = await res.json();
    return mapApiAgent(data);
  }, []);

  // 初始加载
  useEffect(() => {
    refresh();
    loadModels();
  }, [refresh, loadModels]);

  return {
    agents,
    loading,
    error,
    models,
    refresh,
    createAgent,
    updateAgent,
    deleteAgent,
    getAgent,
  };
}
