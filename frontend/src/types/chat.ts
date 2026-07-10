/**
 * 对话相关类型定义
 *
 * 定义消息、会话、事件流等核心数据结构。
 */

import type { Agent } from './agent';

/** 消息角色 */
export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

/** 工具调用状态 */
export type ToolCallStatus = 'pending' | 'running' | 'completed' | 'failed';

/** 工具调用信息 */
export interface ToolCall {
  /** 工具名称 */
  tool: string;
  /** 调用输入参数 */
  input: Record<string, unknown>;
  /** 调用输出结果 */
  output?: string;
  /** 当前状态 */
  status: ToolCallStatus;
  /** 执行耗时（毫秒） */
  durationMs?: number;
}

/** 对话消息 */
export interface Message {
  /** 唯一标识 */
  id: string;
  /** 消息角色 */
  role: MessageRole;
  /** 消息文本内容 */
  content: string;
  /** 发送此消息的 Agent（assistant 角色时） */
  agent?: Agent;
  /** 工具调用信息（tool 角色时） */
  toolCall?: ToolCall;
  /** Token 用量 */
  tokenUsage?: {
    prompt: number;
    completion: number;
  };
  /** 创建时间 */
  createdAt: string;
}

/** 对话会话 */
export interface Session {
  /** 唯一标识 */
  id: string;
  /** 会话标题 */
  title: string;
  /** 当前使用的 Agent */
  agentId: string;
  /** 创建时间 */
  createdAt: string;
  /** 更新时间 */
  updatedAt: string;
}

/** AgentScope 事件类型 */
export type EventType =
  | 'reply_start'
  | 'reply_end'
  | 'model_call_start'
  | 'model_call_end'
  | 'text_block_start'
  | 'text_block_delta'
  | 'text_block_end'
  | 'tool_call_start'
  | 'tool_call_end'
  | 'model_fallback'
  | 'error';

/** AgentScope 事件 */
export interface AgentEvent {
  /** 事件类型 */
  type: EventType;
  /** 事件数据 */
  data: Record<string, unknown>;
  /** 时间戳 */
  timestamp: string;
}
