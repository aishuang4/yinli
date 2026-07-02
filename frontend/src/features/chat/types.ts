import type { ClarificationRequestInfo, DisplaySnapshot, SkillCallInfo, TeamActivityInfo, ToolCallInfo } from '../../hooks/useEventStream';
import type { ChatRuntimeEvent } from '../../types/chatEvents';

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
  /** 工具调用列表（仅 assistant 消息） */
  toolCalls?: ToolCallInfo[];
  /** Skill 应用列表（仅 assistant 消息） */
  skillCalls?: SkillCallInfo[];
  /** Team 协作活动（仅 assistant 消息） */
  teamActivities?: TeamActivityInfo[];
  /** 本轮 Runtime Event 时间线 */
  runtimeEvents?: ChatRuntimeEvent[];
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

export interface PendingTeamSelection {
  message: string;
  images: ChatImagePayload[];
  selectedAgentIds: string[];
  reason: string;
}

export interface ChatSession {
  id: string;
  title: string;
  agent_id: string;
  agent_name: string;
  team_id: string | null;
  team_name: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
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
  toolCalls?: ToolCallInfo[];
  tool_calls?: ToolCallInfo[];
  skillCalls?: SkillCallInfo[];
  skill_calls?: SkillCallInfo[];
  teamActivities?: TeamActivityInfo[];
  team_activities?: StoredTeamActivity[];
  runtimeEvents?: ChatRuntimeEvent[];
  runtime_events?: ChatRuntimeEvent[];
  displaySnapshot?: DisplaySnapshot;
  display_snapshot?: DisplaySnapshot;
  clarificationRequests?: ClarificationRequestInfo[];
  clarification_requests?: ClarificationRequestInfo[];
  clarificationResolved?: boolean;
  clarification_resolved?: boolean;
  created_at: string;
}

export type StoredTeamActivity = Partial<TeamActivityInfo> & {
  agent_id?: string;
  agent_name?: string;
  expected_output?: string;
  skipped_reason?: string;
  report_review_status?: string;
  report_review_notes?: string;
  report_review_gaps?: string[];
  tool_calls?: ToolCallInfo[];
  created_at?: string;
};
