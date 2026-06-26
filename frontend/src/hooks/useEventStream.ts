/**
 * useEventStream — AgentScope SSE 事件流 Hook
 *
 * 通过 fetch + ReadableStream 订阅后端 SSE 事件流，
 * 解析 AgentScope 2.0 的 EventType 并更新 UI 状态。
 *
 * 稳定事件契约见 docs/chat-sse-event-contract.md。底层会透传部分 AgentScope 事件：
 * - REPLY_START / REPLY_END
 * - TEXT_BLOCK_DELTA
 * - THINKING_BLOCK_DELTA
 * - TOOL_CALL_START / TOOL_CALL_END
 * - TOOL_RESULT_START / TOOL_RESULT_TEXT_DELTA / TOOL_RESULT_END
 * - MODEL_CALL_START / MODEL_CALL_END
 * - ERROR
 *
 * @see docs/chat-sse-event-contract.md
 */

import { useCallback, useRef, useState } from 'react';
import { createId } from '../utils/id';

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
  /** 思考链内容（thinking tokens） */
  thinkingContent: string;
  /** 工具调用列表 */
  toolCalls: ToolCallInfo[];
  /** Skill 注入/应用列表 */
  skillCalls: SkillCallInfo[];
  /** Team 协作活动 */
  teamActivities: TeamActivityInfo[];
  /** 模型缓存事件 */
  modelCaches: ModelCacheInfo[];
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
}

function normalizeToolStatus(status: unknown): ToolCallInfo['status'] {
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

function normalizeToolCallRecord(record: Record<string, unknown>): ToolCallInfo {
  return {
    id: String(record.id || record.tool_call_id || createId()),
    name: normalizeToolDisplayName(String(record.name || record.tool_call_name || 'tool')),
    args: String(record.args || ''),
    result: String(record.result || ''),
    status: normalizeToolStatus(record.status),
  };
}

function normalizeToolDisplayName(name: string): string {
  const value = String(name || 'tool');
  const parts = value.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return value;
}

function mergeToolCalls(current: ToolCallInfo[], incoming: ToolCallInfo[]): ToolCallInfo[] {
  const merged = [...current];
  for (const next of incoming) {
    const existingIndex = merged.findIndex((item) => (
      item.id === next.id ||
      (
        item.name === next.name &&
        item.args === next.args &&
        item.result === next.result
      )
    ));
    if (existingIndex === -1) {
      merged.push(next);
      continue;
    }
    const existing = merged[existingIndex];
    merged[existingIndex] = {
      ...existing,
      ...next,
      args: next.args || existing.args,
      result: next.result || existing.result,
      status: next.status || existing.status,
    };
  }
  return merged;
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

/** Team 协作活动信息 */
export interface TeamActivityInfo {
  id: string;
  agentId: string;
  agentName: string;
  role: string;
  status: 'pending' | 'running' | 'waiting' | 'completed' | 'failed';
  task: string;
  detail: string;
  output: string;
  thinking: string;
  currentEvent: string;
  toolCalls: ToolCallInfo[];
  executionMode: string;
  dependsOn: string[];
  waitReason: string;
  dependencyOutputCount: number;
  leaderContextCount: number;
  createdAt: string;
}

/** 模型缓存信息 */
export interface ModelCacheInfo {
  id: string;
  modelName: string;
  inputTokens: number;
  outputTokens: number;
  cacheCreationInputTokens: number;
  cacheInputTokens: number;
  cacheHitRate: number;
  estimatedSavedInputTokens: number;
  createdAt: string;
}

/** 人机交互请求 */
export interface HitlRequestInfo {
  interactionId: string;
  type: string;
  sessionId: string;
  agentId: string;
  agentName: string;
  runId: string;
  scope: 'leader' | 'worker';
  teamActivityId: string | null;
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
interface UseEventStreamReturn {
  /** 流式状态 */
  state: StreamState;
  /** 发送消息并开始接收事件流 */
  sendMessage: (message: string, options?: SendMessageOptions) => Promise<void>;
  /** 恢复一个暂停的人机交互 */
  resumeInteraction: (
    interactionId: string,
    action: 'approve' | 'reject',
    externalResults?: Record<string, string>
  ) => Promise<void>;
  /** 中止当前流 */
  abort: () => Promise<void>;
  /** 重置状态 */
  reset: () => void;
}

/** 发送消息选项 */
interface SendMessageOptions {
  agentId?: string;
  agentName?: string;
  teamId?: string;
  teamName?: string;
  teamMembers?: Array<{
    id?: string;
    agentId: string;
    name: string;
    role: string;
    description: string;
    executionMode?: string;
    dependsOn?: string[];
    handoffPrompt?: string;
  }>;
  systemPrompt?: string;
  model?: string;
  sessionId?: string;
  skillIds?: string[];
  deepThinking?: boolean;
  sandbox?: boolean;
  images?: Array<{
    name: string;
    media_type: string;
    data: string;
  }>;
}

/** 初始状态 */
const INITIAL_STATE: StreamState = {
  isStreaming: false,
  currentRunId: '',
  runStartedAt: '',
  runCompletedAt: '',
  textContent: '',
  thinkingContent: '',
  toolCalls: [],
  skillCalls: [],
  teamActivities: [],
  modelCaches: [],
  hitlRequests: [],
  clarificationRequests: [],
  modelName: '',
  tokenUsage: null,
  error: null,
};

function hasPendingUserInteraction(state: StreamState): boolean {
  return state.hitlRequests.length > 0 || state.clarificationRequests.length > 0;
}

function completeStreamIfNoPendingUserInteraction(state: StreamState): StreamState {
  if (hasPendingUserInteraction(state)) {
    return {
      ...state,
      isStreaming: false,
    };
  }
  return {
    ...state,
    isStreaming: false,
    runCompletedAt: state.runCompletedAt || new Date().toISOString(),
  };
}

/**
 * useEventStream Hook
 *
 * 管理 SSE 事件流的完整生命周期：发送请求 → 接收事件 → 更新状态
 */
export function useEventStream(): UseEventStreamReturn {
  const [state, setState] = useState<StreamState>(INITIAL_STATE);
  const abortControllerRef = useRef<AbortController | null>(null);
  const suppressClarificationTextRef = useRef(false);
  const suppressClarificationThinkingRef = useRef(false);

  const readEventStream = useCallback(async (response: Response) => {
    if (!response.body) {
      throw new Error('Response body is null');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop() || '';

      for (const eventStr of events) {
        if (!eventStr.trim()) continue;
        const parsed = parseSSEEvent(eventStr);
        if (parsed) {
          handleEvent(parsed);
        }
      }
    }
  }, []);

  /** 重置状态到初始值 */
  const reset = useCallback(() => {
    suppressClarificationTextRef.current = false;
    suppressClarificationThinkingRef.current = false;
    setState(INITIAL_STATE);
  }, []);

  /** 中止当前流 */
  const abort = useCallback(async () => {
    const runId = state.currentRunId;
    if (runId) {
      try {
        await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/cancel`, {
          method: 'POST',
        });
      } catch (error) {
        console.warn('[SSE] Failed to cancel run:', error);
      }
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState((prev) => ({
      ...prev,
      isStreaming: false,
      error: prev.error || '用户已停止本轮执行。',
      runCompletedAt: prev.runCompletedAt || new Date().toISOString(),
    }));
  }, [state.currentRunId]);

  /** 发送消息并开始接收事件流 */
  const sendMessage = useCallback(
    async (message: string, options?: SendMessageOptions) => {
      // 中止之前的流（如果有）
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      // 创建新的 AbortController
      const controller = new AbortController();
      abortControllerRef.current = controller;
      suppressClarificationTextRef.current = false;
      suppressClarificationThinkingRef.current = false;

      // 重置状态并开始
      setState({
        ...INITIAL_STATE,
        isStreaming: true,
        runStartedAt: new Date().toISOString(),
      });

      try {
        const response = await fetch('/api/chat/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message,
            agent_id: options?.agentId,
            agent_name: options?.agentName,
            team_id: options?.teamId,
            team_name: options?.teamName,
            team_members: options?.teamMembers,
            system_prompt: options?.systemPrompt,
            model: options?.model,
            session_id: options?.sessionId,
            skill_ids: options?.skillIds,
            deep_thinking: options?.deepThinking,
            sandbox: options?.sandbox,
            images: options?.images,
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        await readEventStream(response);

        // SSE 结束才代表本轮流程全部完成；REPLY_END 后仍可能有收尾事件。
        setState((prev) => completeStreamIfNoPendingUserInteraction(prev));
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          // 用户主动中止
          setState((prev) => completeStreamIfNoPendingUserInteraction(prev));
        } else {
          // 其他错误
          const errorMessage = err instanceof Error ? err.message : String(err);
          setState((prev) => ({
            ...prev,
            isStreaming: false,
            error: errorMessage,
            runCompletedAt: prev.runCompletedAt || new Date().toISOString(),
          }));
        }
      }
    },
    [readEventStream]
  );

  /** 恢复暂停的人机交互 */
  const resumeInteraction = useCallback(
    async (
      interactionId: string,
      action: 'approve' | 'reject',
      externalResults: Record<string, string> = {}
    ) => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;
      setState((prev) => ({
        ...prev,
        isStreaming: true,
        error: null,
        hitlRequests: prev.hitlRequests.filter((item) => item.interactionId !== interactionId),
      }));

      try {
        const response = await fetch('/api/chat/resume', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            interactionId,
            action,
            externalResults,
          }),
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        await readEventStream(response);
        setState((prev) => completeStreamIfNoPendingUserInteraction(prev));
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          setState((prev) => completeStreamIfNoPendingUserInteraction(prev));
        } else {
          const errorMessage = err instanceof Error ? err.message : String(err);
          setState((prev) => ({
            ...prev,
            isStreaming: false,
            error: errorMessage,
            runCompletedAt: prev.runCompletedAt || new Date().toISOString(),
          }));
        }
      }
    },
    [readEventStream]
  );

  /**
   * 解析单个 SSE 事件字符串
   *
   * SSE 格式：
   *   event: TEXT_BLOCK_DELTA
   *   data: {"delta": "你好", ...}
   */
  function parseSSEEvent(raw: string): SSEEvent | null {
    let eventType = '';
    let dataStr = '';

    for (const line of raw.split('\n')) {
      if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        dataStr = line.slice(6);
      }
    }

    if (!eventType || !dataStr) return null;

    try {
      const data = JSON.parse(dataStr);
      return { type: eventType, data };
    } catch {
      console.warn('[SSE] Failed to parse event data:', dataStr);
      return null;
    }
  }

  /**
   * 处理单个 SSE 事件 — 根据事件类型更新状态
   *
   * 遵循 rerender-functional-setstate 规则：
   * 使用函数式 setState 确保基于最新状态更新
   */
  function handleEvent(event: SSEEvent) {
    setState((prev) => {
      switch (event.type) {
        case 'RUN':
          return {
            ...prev,
            currentRunId: String(event.data.run_id || ''),
            runStartedAt: String(event.data.created_at || event.data.createdAt || prev.runStartedAt || new Date().toISOString()),
          };

        // === 文本内容增量 ===
        case 'TEXT_BLOCK_DELTA': {
          if (suppressClarificationTextRef.current) return prev;
          const nextText = prev.textContent + (event.data.delta as string || '');
          const clarificationIndex = findClarificationStartIndex(nextText);
          if (clarificationIndex >= 0) {
            suppressClarificationTextRef.current = true;
          }
          return {
            ...prev,
            textContent: clarificationIndex >= 0 ? nextText.slice(0, clarificationIndex).trimEnd() : nextText,
          };
        }

        // === 思考链增量 ===
        case 'THINKING_BLOCK_DELTA': {
          if (suppressClarificationThinkingRef.current) return prev;
          const nextThinking = prev.thinkingContent + (event.data.delta as string || '');
          const clarificationIndex = findClarificationStartIndex(nextThinking);
          if (clarificationIndex >= 0) {
            suppressClarificationThinkingRef.current = true;
          }
          return {
            ...prev,
            thinkingContent: clarificationIndex >= 0 ? nextThinking.slice(0, clarificationIndex).trimEnd() : nextThinking,
          };
        }

        // === 工具调用开始 ===
        case 'TOOL_CALL_START':
          return {
            ...prev,
            toolCalls: mergeToolCalls(prev.toolCalls, [{
              id: String(event.data.tool_call_id || createId()),
              name: normalizeToolDisplayName(String(event.data.tool_call_name || 'tool')),
              args: '',
              result: '',
              status: 'calling',
            }]),
          };

        // === Skill 上下文注入 ===
        case 'SKILL_CONTEXT': {
          const skills = Array.isArray(event.data.skills) ? event.data.skills : [];
          return {
            ...prev,
            skillCalls: skills.map((item) => {
              const skill = item as Record<string, unknown>;
              return {
                id: String(skill.id || skill.name || createId()),
                name: String(skill.name || skill.id || 'Skill'),
                description: String(skill.description || ''),
                trigger: String(skill.trigger || ''),
                content: String(skill.content || ''),
                resourcePath: String(skill.resourcePath || ''),
                resources: Array.isArray(skill.resources)
                  ? skill.resources.map((item) => String(item))
                  : [],
                status: 'running' as const,
              };
            }),
          };
        }

        // === Skill 使用结束 ===
        case 'SKILL_END': {
          const status = event.data.status === 'failed' ? 'failed' : 'completed';
          const skills = Array.isArray(event.data.skills) ? event.data.skills : [];
          const endedIds = new Set(
            skills.map((item) => String((item as Record<string, unknown>).id || ''))
          );
          return {
            ...prev,
            skillCalls: prev.skillCalls.map((skill) => (
              endedIds.has(skill.id) ? { ...skill, status } : skill
            )),
          };
        }

        // === Team 协作活动 ===
        case 'TEAM_ACTIVITY': {
          const activities = Array.isArray(event.data.activities) ? event.data.activities : [];
          return {
            ...prev,
            teamActivities: activities.map((item) => {
              const activity = item as Record<string, unknown>;
              const existing = prev.teamActivities.find((current) => (
                current.id === activity.id || current.agentId === activity.agentId
              ));
              const status = String(activity.status || 'pending');
              const toolCalls = Array.isArray(activity.toolCalls)
                ? mergeToolCalls(
                  existing?.toolCalls || [],
                  activity.toolCalls.map((toolCall) => normalizeToolCallRecord(toolCall as Record<string, unknown>))
                )
                : existing?.toolCalls || [];
              return {
                id: String(activity.id || createId()),
                agentId: String(activity.agentId || ''),
                agentName: String(activity.agentName || 'Agent'),
                role: String(activity.role || 'worker'),
                status: (
                  status === 'running' || status === 'waiting' || status === 'completed' || status === 'failed'
                    ? status
                    : 'pending'
                ),
                task: String(activity.task || ''),
                detail: String(activity.detail || ''),
                output: String(activity.output || existing?.output || ''),
                thinking: String(activity.thinking || existing?.thinking || ''),
                currentEvent: existing?.currentEvent || '',
                toolCalls,
                executionMode: String(activity.executionMode || existing?.executionMode || 'parallel'),
                dependsOn: Array.isArray(activity.dependsOn)
                  ? activity.dependsOn.map((item) => String(item))
                  : existing?.dependsOn || [],
                waitReason: String(activity.waitReason || existing?.waitReason || ''),
                dependencyOutputCount: Number(activity.dependencyOutputCount ?? existing?.dependencyOutputCount ?? 0),
                leaderContextCount: Number(activity.leaderContextCount ?? existing?.leaderContextCount ?? 0),
                createdAt: String(activity.createdAt || ''),
              };
            }),
          };
        }

        // === Team 成员自己的 ReAct 事件 ===
        case 'TEAM_MEMBER_EVENT': {
          const activityId = String(event.data.activityId || '');
          const agentId = String(event.data.agentId || '');
          const eventType = String(event.data.eventType || '');
          const memberEvent = (
            typeof event.data.event === 'object' && event.data.event !== null
              ? event.data.event as Record<string, unknown>
              : {}
          );

          return {
            ...prev,
            teamActivities: prev.teamActivities.map((activity) => {
              if (activity.id !== activityId && activity.agentId !== agentId) {
                return activity;
              }

              if (eventType === 'TEXT_BLOCK_DELTA') {
                return {
                  ...activity,
                  status: 'running',
                  output: activity.output + String(memberEvent.delta || ''),
                  currentEvent: '输出结论',
                };
              }
              if (eventType === 'THINKING_BLOCK_DELTA') {
                return {
                  ...activity,
                  status: 'running',
                  thinking: activity.thinking + String(memberEvent.delta || ''),
                  currentEvent: '思考中',
                };
              }
              if (eventType === 'TOOL_CALL_START') {
                return {
                  ...activity,
                  status: 'running',
                  currentEvent: `调用工具 ${normalizeToolDisplayName(String(memberEvent.tool_call_name || 'tool'))}`,
                  toolCalls: mergeToolCalls(activity.toolCalls, [{
                    id: String(memberEvent.tool_call_id || createId()),
                    name: normalizeToolDisplayName(String(memberEvent.tool_call_name || 'tool')),
                    args: '',
                    result: '',
                    status: 'calling',
                  }]),
                };
              }
              if (eventType === 'TOOL_CALL_DELTA') {
                return {
                  ...activity,
                  toolCalls: activity.toolCalls.map((toolCall) => (
                    toolCall.id === memberEvent.tool_call_id
                      ? { ...toolCall, args: toolCall.args + String(memberEvent.delta || '') }
                      : toolCall
                  )),
                };
              }
              if (eventType === 'TOOL_CALL_END' || eventType === 'TOOL_RESULT_START') {
                return {
                  ...activity,
                  currentEvent: '等待工具结果',
                  toolCalls: activity.toolCalls.map((toolCall) => (
                    toolCall.id === memberEvent.tool_call_id
                      ? { ...toolCall, status: 'running' }
                      : toolCall
                  )),
                };
              }
              if (eventType === 'TOOL_RESULT_TEXT_DELTA') {
                return {
                  ...activity,
                  currentEvent: '读取工具结果',
                  toolCalls: activity.toolCalls.map((toolCall) => (
                    toolCall.id === memberEvent.tool_call_id
                      ? { ...toolCall, result: toolCall.result + String(memberEvent.delta || '') }
                      : toolCall
                  )),
                };
              }
              if (eventType === 'TOOL_RESULT_END') {
                return {
                  ...activity,
                  toolCalls: activity.toolCalls.map((toolCall) => (
                    toolCall.id === memberEvent.tool_call_id
                      ? { ...toolCall, status: 'completed' }
                      : toolCall
                  )),
                };
              }
              if (eventType === 'MODEL_CALL_START') {
                return {
                  ...activity,
                  status: 'running',
                  currentEvent: `调用模型 ${String(memberEvent.model_name || '')}`.trim(),
                };
              }
              if (eventType === 'REPLY_START') {
                return {
                  ...activity,
                  status: 'running',
                  currentEvent: '开始 ReAct 循环',
                };
              }
              if (eventType === 'REPLY_END') {
                return {
                  ...activity,
                  status: 'completed',
                  currentEvent: 'ReAct 循环完成',
                };
              }
              return activity;
            }),
          };
        }

        // === 人机交互请求 ===
        case 'HITL_REQUIRED': {
          const toolCalls = Array.isArray(event.data.toolCalls) ? event.data.toolCalls : [];
          const interactionId = String(event.data.interactionId || '');
          const stringifyToolInput = (input: unknown) => {
            if (typeof input === 'string') return input;
            if (input === null || input === undefined) return '';
            try {
              return JSON.stringify(input);
            } catch {
              return String(input);
            }
          };
          return {
            ...prev,
            isStreaming: false,
            hitlRequests: prev.hitlRequests.some((item) => item.interactionId === interactionId)
              ? prev.hitlRequests
              : [
                  ...prev.hitlRequests,
              {
                interactionId,
                type: String(event.data.type || ''),
                sessionId: String(event.data.sessionId || ''),
                agentId: String(event.data.agentId || ''),
                agentName: String(event.data.agentName || 'Agent'),
                runId: String(event.data.runId || event.data.run_id || prev.currentRunId || ''),
                scope: event.data.scope === 'worker' ? 'worker' : 'leader',
                teamActivityId: event.data.teamActivityId ? String(event.data.teamActivityId) : null,
                replyId: String(event.data.replyId || ''),
                createdAt: String(event.data.createdAt || ''),
                toolCalls: toolCalls.map((item) => {
                  const toolCall = item as Record<string, unknown>;
                  const rawName = String(toolCall.name || 'tool');
                  return {
                    id: String(toolCall.id || createId()),
                    name: normalizeToolDisplayName(rawName),
                    rawName,
                    input: stringifyToolInput(toolCall.input),
                  };
                }),
              },
            ],
          };
        }

        // === 工具调用参数增量 ===
        case 'TOOL_CALL_DELTA':
          return {
            ...prev,
            toolCalls: prev.toolCalls.map((tc) =>
              tc.id === event.data.tool_call_id
                ? { ...tc, args: tc.args + (event.data.delta as string || '') }
                : tc
            ),
          };

        // === 工具调用结束 ===
        case 'TOOL_CALL_END':
          return {
            ...prev,
            toolCalls: prev.toolCalls.map((tc) =>
              tc.id === event.data.tool_call_id
                ? { ...tc, status: 'running' }
                : tc
            ),
          };

        // === 工具结果开始 ===
        case 'TOOL_RESULT_START':
          return {
            ...prev,
            toolCalls: prev.toolCalls.map((tc) =>
              tc.id === event.data.tool_call_id
                ? { ...tc, status: 'running' }
                : tc
            ),
          };

        // === 工具结果文本增量 ===
        case 'TOOL_RESULT_TEXT_DELTA':
          return {
            ...prev,
            toolCalls: prev.toolCalls.map((tc) =>
              tc.id === event.data.tool_call_id
                ? { ...tc, result: tc.result + (event.data.delta as string || '') }
                : tc
            ),
          };

        // === 工具结果结束 ===
        case 'TOOL_RESULT_END':
          return {
            ...prev,
            toolCalls: prev.toolCalls.map((tc) =>
              tc.id === event.data.tool_call_id
                ? { ...tc, status: 'completed' }
                : tc
            ),
          };

        // === 模型调用信息 ===
        case 'MODEL_CALL_START':
          return {
            ...prev,
            modelName: (event.data.model_name as string) || prev.modelName,
          };

        // === 模型调用结束（Token 用量） ===
        case 'MODEL_CALL_END': {
          const inputTokens = (event.data.input_tokens as number) || 0;
          const outputTokens = (event.data.output_tokens as number) || 0;
          const cacheCreationInputTokens = (event.data.cache_creation_input_tokens as number) || 0;
          const cacheInputTokens = (event.data.cache_input_tokens as number) || 0;
          const cacheBaseTokens = inputTokens + cacheInputTokens;
          const modelCache: ModelCacheInfo = {
            id: `provider-cache-${Date.now()}`,
            modelName: prev.modelName,
            inputTokens,
            outputTokens,
            cacheCreationInputTokens,
            cacheInputTokens,
            cacheHitRate: cacheBaseTokens > 0 ? cacheInputTokens / cacheBaseTokens : 0,
            estimatedSavedInputTokens: cacheInputTokens,
            createdAt: new Date().toISOString(),
          };
          return {
            ...prev,
            tokenUsage: {
              input: inputTokens,
              output: outputTokens,
            },
            modelCaches: [...prev.modelCaches, modelCache],
          };
        }

        case 'CLARIFICATION_REQUIRED':
          suppressClarificationTextRef.current = false;
          suppressClarificationThinkingRef.current = false;
          return {
            ...prev,
            isStreaming: false,
            textContent: '',
            thinkingContent: '',
            clarificationRequests: [
              ...prev.clarificationRequests,
              {
                id: String(event.data.id || Date.now()),
                title: String(event.data.title || '需要补充信息'),
                description: String(event.data.description || ''),
                questions: (event.data.questions as ClarificationQuestionInfo[]) || [],
                createdAt: String(event.data.createdAt || new Date().toISOString()),
              },
            ],
          };

        // === 错误事件 ===
        case 'ERROR':
          return {
            ...prev,
            isStreaming: false,
            runCompletedAt: prev.runCompletedAt || new Date().toISOString(),
            error: (event.data.message as string) || 'Unknown error',
          };

        case 'RUN_CANCELLED':
          return {
            ...prev,
            isStreaming: false,
            runCompletedAt: String(event.data.completed_at || event.data.completedAt || new Date().toISOString()),
            error: '用户已停止本轮执行。',
          };

        // === 回复结束 ===
        case 'REPLY_END':
          return prev;

        // === 其他事件（REPLY_START, TEXT_BLOCK_START 等）— 不需要特殊处理 ===
        default:
          return prev;
      }
    });
  }

  return { state, sendMessage, resumeInteraction, abort, reset };
}

function findClarificationStartIndex(text: string): number {
  const tagIndex = text.indexOf('<clarification');
  if (tagIndex >= 0) return tagIndex;

  const jsonMatch = text.match(/\{\s*"title"\s*:/);
  return jsonMatch?.index ?? -1;
}
