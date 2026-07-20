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
  /** 核采样参数 (0-1) */
  topP: number;
  /** 最大输出 token 数 */
  maxTokens: number;
  /** ReAct 最大循环次数 */
  maxReactIters: number;
}

/** 安全边界配置 */
export type ToolPermissionProfile = 'read_only' | 'trusted_tools' | 'confirm_destructive' | 'ask_every_time';

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
  /** 工具权限配置 */
  toolPermissionProfile: ToolPermissionProfile;
  /** 工具执行后端 */
  executionBackend: 'local' | 'docker';
  /** Docker 沙盒基础镜像 */
  dockerImage: string;
  /** Docker 沙盒是否持久化 */
  persistentSandbox: boolean;
  /** 工具调用超时（秒） */
  timeoutSeconds: number;
}

/** 历史 Plan 配置兼容字段：旧 runtime.kind=plan 已下线，当前 Plan Mode middleware 不消费。 */
export interface PlanConfig {
  /** 历史兼容字段：旧计划层开关 */
  enabled: boolean;
  /** 历史兼容字段：旧计划模式 */
  mode: 'lightweight' | 'structured' | 'strict';
  /** 历史兼容字段：旧最大计划步骤数 */
  maxSteps: number;
  /** 历史兼容字段：旧工具前计划开关 */
  requirePlanBeforeTools: boolean;
  /** 历史兼容字段：旧计划审批开关 */
  requireUserApproval: boolean;
  /** 历史兼容字段：旧重规划开关 */
  allowReplanning: boolean;
}

/** Agent 完整配置 */
export interface Agent {
  /** 唯一标识 */
  id: string;
  /** 持久资源所属 Workspace。 */
  workspaceId?: string;
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
  /** 知识库 HTTP 工具 ID 列表 */
  knowledgeTools: string[];
  /** Skills ID 列表 */
  skills: string[];
  /** 标签 */
  tags: string[];
  /** 历史 Plan 配置兼容字段 */
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
  primary: 'deepseek-v4-flash',
  fallback: ['deepseek-v4-pro'],
  temperature: 0.7,
  topP: 0.8,
  maxTokens: 4096,
  maxReactIters: 40,
};

/** 默认安全边界 */
export const DEFAULT_GUARDRAILS: Guardrails = {
  filesystemScope: '',
  shellBlacklist: ['rm -rf /', 'sudo'],
  networkEnabled: true,
  humanApproval: false,
  toolPermissionMode: 'accept_edits',
  toolPermissionProfile: 'confirm_destructive',
  executionBackend: 'local',
  dockerImage: 'python:3.11-slim',
  persistentSandbox: false,
  timeoutSeconds: 300,
};

/** 默认历史 Plan 配置兼容字段 */
export const DEFAULT_PLAN_CONFIG: PlanConfig = {
  enabled: false,
  mode: 'structured',
  maxSteps: 6,
  requirePlanBeforeTools: false,
  requireUserApproval: false,
  allowReplanning: false,
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
    knowledgeTools: [],
    skills: [],
    tags: [],
    planConfig: { ...DEFAULT_PLAN_CONFIG },
    guardrails: { ...DEFAULT_GUARDRAILS },
  };
}
