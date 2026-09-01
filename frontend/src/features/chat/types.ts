import type { ClarificationRequestInfo } from '../../hooks/useEventStream';
import type { DisplaySnapshot } from '../runtime-stream/protocol';

/** 本地消息记录 */
export interface LocalMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  /** 消息创建时间 */
  createdAt?: string;
  /** Agent 处理开始时间 */
  processStartedAt?: string;
  /** Agent 处理结束时间 */
  processCompletedAt?: string;
  /** 后端投影后的消息展示快照 */
  displaySnapshot?: DisplaySnapshot;
  /** 思考链内容（仅 assistant 消息） */
  thinking?: string;
  /** ReAct 过程文本（仅本地 live run 收口消息） */
  reasoning?: string;
  /** Token 用量 */
  tokenUsage?: { input: number; output: number } | null;
  /** 使用的模型 */
  modelName?: string;
  /** 错误信息 */
  error?: string;
  /** 图片输入 */
  images?: ChatImagePayload[];
  /** 用户上传到当前 Session 的普通附件 */
  files?: ChatFilePayload[];
  /** 本轮暂停时模型提出的补充信息请求 */
  clarificationRequests?: ClarificationRequestInfo[];
  /** 补充信息已提交，仅保留模型问题作为上下文 */
  clarificationResolved?: boolean;
}

export interface ChatImagePayload {
  name: string;
  media_type: string;
  data: string;
}

export interface ChatImageAttachment extends ChatImagePayload {
  id: string;
  previewUrl: string;
}

export interface ChatFilePayload {
  name: string;
  path: string;
  size: number;
  media_type: string;
}

export interface ChatFileAttachment {
  id: string;
  file: File;
  name: string;
  size: number;
}

export interface ChatSessionGroupStudioHint {
  id: string;
  status: string;
  title: string;
}

export interface ChatSession {
  id: string;
  title: string;
  agent_id: string;
  agent_name: string;
  mode: 'agent' | 'team';
  workspace_id: string;
  project_id: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  groupStudio?: ChatSessionGroupStudioHint | null;
  group_studio?: ChatSessionGroupStudioHint | null;
}

export interface StoredChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  agent_id: string | null;
  agent_name: string | null;
  model_name: string | null;
  error: string | null;
  thinking?: string;
  images: ChatImagePayload[];
  files: ChatFilePayload[];
  displaySnapshot?: DisplaySnapshot;
  display_snapshot?: DisplaySnapshot;
  clarificationRequests?: ClarificationRequestInfo[];
  clarification_requests?: ClarificationRequestInfo[];
  clarificationResolved?: boolean;
  clarification_resolved?: boolean;
  created_at: string;
}
