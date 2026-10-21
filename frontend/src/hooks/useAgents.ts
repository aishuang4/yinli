/**
 * useAgents — Agent CRUD 操作 Hook
 *
 * 封装 Agent 相关的 API 调用和状态管理。
 * 使用 SWR 风格的缓存策略（MVP 阶段手动实现）。
 *
 * @see 后端 API: GET/POST/PUT/DELETE /api/agents
 */

import { useCallback, useEffect } from 'react';
import type {
  Agent,
  AgentCreatePayload,
  AgentUpdatePayload,
} from '../types/agent';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';
import { mapApiAgent, useAgentsStore } from '../stores/agentsStore';

/** Hook 返回值 */
interface UseAgentsReturn {
  /** Agent 列表 */
  agents: Agent[];
  /** 是否正在加载 */
  loading: boolean;
  /** 错误信息 */
  error: string | null;

  /** 刷新 Agent 列表。silent 时不把已有列表打成 loading，避免 Chat 下拉被冻住。 */
  refresh: (options?: { silent?: boolean }) => Promise<void>;
  /** 创建 Agent */
  createAgent: (data: AgentCreatePayload) => Promise<Agent>;
  /** 更新 Agent */
  updateAgent: (id: string, data: AgentUpdatePayload) => Promise<Agent>;
  /** 删除 Agent */
  deleteAgent: (id: string) => Promise<void>;
  /** 获取单个 Agent */
  getAgent: (id: string) => Promise<Agent>;
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
  if ('role' in data && data.role !== undefined) result.role = data.role;
  if ('jobToolkit' in data && data.jobToolkit !== undefined) result.jobToolkit = data.jobToolkit;
  if ('mcpTools' in data && data.mcpTools !== undefined) result.mcpTools = data.mcpTools;
  if ('knowledgeTools' in data && data.knowledgeTools !== undefined) result.knowledgeTools = data.knowledgeTools;
  if ('planConfig' in data && data.planConfig) {
    result.planConfig = {
      enabled: data.planConfig.enabled,
      mode: data.planConfig.mode,
      maxSteps: data.planConfig.maxSteps,
      requirePlanBeforeTools: data.planConfig.requirePlanBeforeTools,
      requireUserApproval: data.planConfig.requireUserApproval,
      allowReplanning: data.planConfig.allowReplanning,
    };
  }

  if ('modelConfig' in data && data.modelConfig) {
    result.modelConfig = {
      max_react_iters: data.modelConfig.maxReactIters,
    };
  }

  if ('guardrails' in data && data.guardrails) {
    const guardrails: Record<string, unknown> = {
      shell_blacklist: data.guardrails.shellBlacklist,
      network_enabled: data.guardrails.networkEnabled,
      execution_backend: data.guardrails.executionBackend,
      docker_image: data.guardrails.dockerImage,
      persistent_sandbox: data.guardrails.persistentSandbox,
      timeout_seconds: data.guardrails.timeoutSeconds,
    };
    if (data.guardrails.filesystemScope.trim()) {
      guardrails.filesystem_scope = data.guardrails.filesystemScope;
    }
    result.guardrails = guardrails;
  }

  return result;
}

/**
 * useAgents Hook
 */
export function useAgents(): UseAgentsReturn {
    const agents = useAgentsStore((state) => state.agents);
  const loading = useAgentsStore((state) => state.loading);
  const error = useAgentsStore((state) => state.error);
  const upsertAgent = useAgentsStore((state) => state.upsertAgent);
  const removeAgent = useAgentsStore((state) => state.removeAgent);

  const refresh = useCallback(async (options?: { silent?: boolean }) => {
    await useAgentsStore.getState().syncFromServer(options);
  }, []);

  /** 创建 Agent */
  const createAgent = useCallback(async (payload: AgentCreatePayload): Promise<Agent> => {
    try {
      const res = await fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mapToApiPayload({ ...payload,  })),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const agent = mapApiAgent(await res.json());
      upsertAgent(agent);
      notifySuccess('Agent 创建成功');
      return agent;
    } catch (err) {
      notifyError(`Agent 创建失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [upsertAgent]);

  /** 更新 Agent */
  const updateAgent = useCallback(async (id: string, payload: AgentUpdatePayload): Promise<Agent> => {
    try {
      const res = await fetch(`/api/agents/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mapToApiPayload(payload)),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const agent = mapApiAgent(await res.json());
      upsertAgent(agent);
      notifySuccess('Agent 保存成功');
      return agent;
    } catch (err) {
      notifyError(`Agent 保存失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [upsertAgent]);

  /** 删除 Agent */
  const deleteAgent = useCallback(async (id: string): Promise<void> => {
    try {
      const res = await fetch(`/api/agents/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      removeAgent(id);
      notifySuccess('Agent 删除成功');
    } catch (err) {
      notifyError(`Agent 删除失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [removeAgent]);

  /** 获取单个 Agent */
  const getAgent = useCallback(async (id: string): Promise<Agent> => {
    const res = await fetch(`/api/agents/${id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return mapApiAgent(await res.json());
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);


  return {
    agents,
    loading,
    error,
    refresh,
    createAgent,
    updateAgent,
    deleteAgent,
    getAgent,
  };
}
