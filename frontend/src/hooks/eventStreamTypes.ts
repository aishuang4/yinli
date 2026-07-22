/** Chat 事件流 Hook 的公开状态与请求类型。 */

import type { DisplaySnapshot } from '../types/displayEvents';
import type { ChatRunPlanState } from '../types/runState';
import type { SubagentTimelineItem } from '../types/subagent';
import type { TeamConsoleState } from '../types/teamRuntime';


/** SSE 事件类型 */
export interface SSEEvent {
  type: string;
  data: Record<string, unknown>;
}

/** 流式状态 */
export interface StreamState {
  /** 是否正在接收事件流 */
  isStreaming: boolean;
  /** 当前后台 Run ID */
  currentRunId: string;
  /** 当前 Run 开始时间 */
  runStartedAt: string;
  /** 当前 Run 结束时间 */
  runCompletedAt: string;
  /** 累积的文本内容 */
  textContent: string;
  /** ReAct 过程文本内容 */
  reasoningContent: string;
  /** 最终答案文本内容 */
  finalAnswerContent: string;
  /** 思考链内容（thinking tokens） */
  thinkingContent: string;
  /** 工具调用列表 */
  toolCalls: ToolCallInfo[];
  /** Skill 注入/应用列表 */
  skillCalls: SkillCallInfo[];
  /** 与单 Agent DisplaySnapshot 完全隔离的 Team Console 状态。 */
  teamConsole: TeamConsoleState;
  /** RunState 投影出的 Plan Mode 状态 */
  plans: ChatRunPlanState[];
  /** 单 Agent 主展示快照 */
  displaySnapshot: DisplaySnapshot;
  /** 待人工处理的交互 */
  hitlRequests: HitlRequestInfo[];
  /** 待用户补充信息的反问 */
  clarificationRequests: ClarificationRequestInfo[];
  /** 当前正在调用的模型名称 */
  modelName: string;
  /** Token 用量 */
  tokenUsage: { input: number; output: number } | null;
  /** 错误信息 */
  error: string | null;
}

/** 工具调用信息 */
export interface ToolCallInfo {
  /** 工具调用 ID */
  id: string;
  /** 工具名称 */
  name: string;
  /** 调用参数（JSON 字符串） */
  args: string;
  /** 执行结果 */
  result: string;
  /** 执行状态 */
  status: 'calling' | 'running' | 'completed' | 'failed';
  subagentName?: string;
  subagentInvocationId?: string;
  subagentStatus?: string;
  subagentTimeline?: SubagentTimelineItem[];
  subagentSummary?: string;
  subagentError?: string;
}

export function normalizeToolStatus(status: unknown): ToolCallInfo['status'] {
  const value = String(status || 'calling');
  return (
    value === 'running' ||
    value === 'completed' ||
    value === 'failed' ||
    value === 'calling'
      ? value
      : 'calling'
  );
}

export function normalizeToolDisplayName(name: string): string {
  const value = String(name || 'tool');
  const parts = value.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return value;
}

/** Skill 应用信息 */
export interface SkillCallInfo {
  id: string;
  name: string;
  description: string;
  trigger: string;
  content: string;
  resourcePath: string;
  resources: string[];
  status: 'running' | 'completed' | 'failed';
}

/** 人机交互请求 */
export interface HitlRequestInfo {
  interactionId: string;
  type: string;
  sessionId: string;
  agentId: string;
  agentName: string;
  runId: string;
  scope: 'leader';
  replyId: string;
  toolCalls: Array<{
    id: string;
    name: string;
    input: string;
    rawName?: string;
  }>;
  createdAt: string;
}

export interface ClarificationQuestionInfo {
  id: string;
  type: 'choice' | 'multi_choice' | 'text';
  label: string;
  options: string[];
  placeholder: string;
  required: boolean;
}

export interface ClarificationRequestInfo {
  id: string;
  title: string;
  description: string;
  questions: ClarificationQuestionInfo[];
  createdAt: string;
}

/** Hook 返回值 */
export interface UseEventStreamReturn {
  /** 流式状态 */
  state: StreamState;
  /** 发送消息并开始接收事件流 */
  sendMessage: (message: string, options?: SendMessageOptions) => Promise<void>;
  /** 恢复一个暂停的人机交互 */
  resumeInteraction: (
    interactionId: string,
    action: 'approve' | 'reject',
    externalResults?: Record<string, string>,
    rememberApproval?: boolean,
  ) => Promise<void>;
  /** 重新订阅一个仍在后台运行或等待人工处理的 Run */
  subscribeRun: (runId: string) => Promise<void>;
  /** 中止当前流 */
  abort: () => Promise<void>;
  /** 重置状态 */
  reset: () => void;
}

/** 发送消息选项 */
export interface SendMessageOptions {
  projectId?: string;
  agentId?: string;
  agentName?: string;
  mode?: 'agent' | 'team';
  systemPrompt?: string;
  model?: string;
  sessionId?: string;
  displayMessage?: string;
  skillIds?: string[];
  deepThinking?: boolean;
  sandbox?: boolean;
  images?: Array<{
    name: string;
    media_type: string;
    data: string;
  }>;
  files?: Array<{
    name: string;
    path: string;
    size: number;
    media_type: string;
  }>;
}
