/**
 * Agent 相关类型定义
 *
 * 对应后端 Agent 配置模型的 TypeScript 类型。
 * 与 backend/src/server/routes/agents.py 的 Pydantic 模型保持同步。
 */

/** Agent 运行状态 */
export type AgentStatus = 'idle' | 'thinking' | 'working' | 'error';

/** 模型配置 */
export interface ModelConfig {
  /** 主模型名称（如 qwen-max） */
  primary: string;
  /** 降级模型链（有序） */
  fallback: string[];
  /** 温度参数 (0-2) */
  temperature: number;
  /** 最大输出 token 数 */
  maxTokens: number;
}

/** 安全边界配置 */
export interface Guardrails {
  /** 文件系统访问范围 */
  filesystemScope: string;
  /** Shell 命令黑名单 */
  shellBlacklist: string[];
  /** 是否允许网络访问 */
  networkEnabled: boolean;
  /** 是否启用人工审批 */
  humanApproval: boolean;
  /** 工具权限模式 */
  toolPermissionMode: 'ask_every_time' | 'accept_edits' | 'bypass';
  /** 工具调用超时（秒） */
  timeoutSeconds: number;
}

/** 计划模型配置 */
export interface PlanConfig {
  /** 是否启用计划层 */
  enabled: boolean;
  /** 计划模式 */
  mode: 'lightweight' | 'structured' | 'strict';
  /** 最大计划步骤数 */
  maxSteps: number;
  /** 调用工具前是否先产出计划 */
  requirePlanBeforeTools: boolean;
  /** 执行计划前是否需要用户确认 */
  requireUserApproval: boolean;
  /** 执行中是否允许修订计划 */
  allowReplanning: boolean;
}

/** Agent 完整配置 */
export interface Agent {
  /** 唯一标识 */
  id: string;
  /** Agent 名称 */
  name: string;
  /** 头像 URL */
  avatar: string;
  /** 一句话描述 */
  description: string;
  /** 系统提示词 */
  systemPrompt: string;
  /** 模型配置 */
  modelConfig: ModelConfig;
  /** MCP 工具 ID 列表 */
  mcpTools: string[];
  /** Skills ID 列表 */
  skills: string[];
  /** 标签 */
  tags: string[];
  /** 可调用的子 Agent ID 列表 */
  callableAgents: string[];
  /** 最大调用深度 */
  maxDepth: number;
  /** 是否在运行时注入 Agent 自己的长期记忆 */
  memoryEnabled: boolean;
  /** 计划模型配置 */
  planConfig: PlanConfig;
  /** 安全边界 */
  guardrails: Guardrails;
  /** 当前运行状态 */
  status: AgentStatus;
  /** 创建时间 */
  createdAt: string;
  /** 更新时间 */
  updatedAt: string;
}

/** 创建 Agent 请求体（不含 id/时间/状态） */
export type AgentCreatePayload = Omit<Agent, 'id' | 'createdAt' | 'updatedAt' | 'status'>;

/** 更新 Agent 请求体（所有字段可选） */
export type AgentUpdatePayload = Partial<AgentCreatePayload>;

/** 可用模型信息 */
export interface ModelOption {
  id: string;
  name: string;
  description: string;
}

/** 模型列表 API 响应 */
export interface ModelsResponse {
  primary: ModelOption[];
  fallback: ModelOption[];
}

/** 默认模型配置 */
export const DEFAULT_MODEL_CONFIG: ModelConfig = {
  primary: 'qwen-plus',
  fallback: ['qwen-turbo'],
  temperature: 0.7,
  maxTokens: 4096,
};

/** 默认安全边界 */
export const DEFAULT_GUARDRAILS: Guardrails = {
  filesystemScope: '/',
  shellBlacklist: ['rm -rf /', 'sudo'],
  networkEnabled: true,
  humanApproval: false,
  toolPermissionMode: 'accept_edits',
  timeoutSeconds: 300,
};

/** 默认计划模型配置 */
export const DEFAULT_PLAN_CONFIG: PlanConfig = {
  enabled: true,
  mode: 'structured',
  maxSteps: 6,
  requirePlanBeforeTools: true,
  requireUserApproval: false,
  allowReplanning: true,
};

/** 创建空 Agent 表单数据 */
export function createEmptyAgentForm(): AgentCreatePayload {
  return {
    name: '',
    avatar: '',
    description: '',
    systemPrompt: 'You are a helpful assistant.',
    modelConfig: { ...DEFAULT_MODEL_CONFIG },
    mcpTools: [],
    skills: [],
    tags: [],
    callableAgents: [],
    maxDepth: 2,
    memoryEnabled: true,
    planConfig: { ...DEFAULT_PLAN_CONFIG },
    guardrails: { ...DEFAULT_GUARDRAILS },
  };
}
