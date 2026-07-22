/**
 * useEventStream — Chat RunState 事件流 Hook
 *
 * Chat 主链路订阅 RunState 中间层 SSE：
 * - RUN_STATE
 * - RUN_STATE_PATCH
 *
 * AgentScope raw SSE 只在后端投影为 RunState 和 Trace。
 * 新 Chat UI 功能不得直接依赖 raw SSE 作为 live 状态来源。
 *
 * @see docs/chat-sse-event-contract.md
 */

import { useCallback, useRef, useState } from 'react';
import type { ChatRunStatePatch, ChatRunStateSnapshot } from '../types/runState';
import { notifySuccess } from '../utils/feedback';
import { useAppStore } from '../stores/appStore';
import {
  applyRunStatePatch,
  completeStreamIfNoPendingUserInteraction,
  failStreamWithError,
  INITIAL_STATE,
  streamStateFromRunState,
} from './runStateStreamAdapter';
import type {
  SendMessageOptions,
  SSEEvent,
  StreamState,
  UseEventStreamReturn,
} from './eventStreamTypes';

export type { ChatRunPlanState, ChatRunPlanStep, DisplaySnapshot } from '../types/runState';
export type {
  ClarificationQuestionInfo,
  ClarificationRequestInfo,
  HitlRequestInfo,
  SendMessageOptions,
  SkillCallInfo,
  SSEEvent,
  StreamState,
  ToolCallInfo,
  UseEventStreamReturn,
} from './eventStreamTypes';

/**
 * useEventStream Hook
 *
 * 管理 SSE 事件流的完整生命周期：发送请求 → 接收事件 → 更新状态
 */
export function useEventStream(): UseEventStreamReturn {
  const activeProjectId = useAppStore((storeState) => storeState.activeProjectId);
  const [state, setState] = useState<StreamState>(INITIAL_STATE);
  const abortControllerRef = useRef<AbortController | null>(null);

  /** 处理 RunState 协议事件，Team 事件由独立的 teamEventReducer 投影。 */
  const handleEvent = useCallback((event: SSEEvent) => {
    if (event.type === 'RUN_STATE_PATCH') {
      const patch = event.data as unknown as ChatRunStatePatch;
      if (patch.kind === 'notice' && patch.notice?.type === 'MEMORY_SKILL_GENERATING') {
        notifySuccess(patch.notice.message || 'Skills 生成中');
      }
    }
    setState((prev) => {
      switch (event.type) {
        case 'RUN_STATE':
          return streamStateFromRunState(event.data as unknown as ChatRunStateSnapshot);

        case 'RUN_STATE_PATCH':
          return applyRunStatePatch(prev, event.data as unknown as ChatRunStatePatch);

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

        default:
          return prev;
      }
    });
  }, []);

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
  }, [handleEvent]);

  /** 重置状态到初始值 */
  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setState(INITIAL_STATE);
  }, []);

  /** 中止当前流 */
  const abort = useCallback(async () => {
    const runId = state.currentRunId;
    if (runId) {
      try {
        const params = new URLSearchParams({ projectId: activeProjectId });
        await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/cancel?${params.toString()}`, {
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
  }, [activeProjectId, state.currentRunId]);

  /** 重新订阅仍在后台运行或等待人工处理的 Run */
  const subscribeRun = useCallback(async (runId: string) => {
    if (!runId) return;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;
    setState((prev) => {
      const isDifferentRun = Boolean(prev.currentRunId && prev.currentRunId !== runId);
      return {
        ...(isDifferentRun ? INITIAL_STATE : prev),
        currentRunId: runId,
        isStreaming: true,
        error: null,
      };
    });

    try {
      const params = new URLSearchParams({ projectId: activeProjectId });
      const response = await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/state/stream?${params.toString()}`, {
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      await readEventStream(response);
      setState((prev) => completeStreamIfNoPendingUserInteraction(prev));
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return;
      }
      const errorMessage = err instanceof Error ? err.message : String(err);
      setState((prev) => ({
        ...prev,
        isStreaming: false,
        error: errorMessage,
        runCompletedAt: prev.runCompletedAt || new Date().toISOString(),
      }));
    }
  }, [activeProjectId, readEventStream]);

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

      // 重置状态并开始
      setState({
        ...INITIAL_STATE,
        isStreaming: true,
        runStartedAt: new Date().toISOString(),
      });

      try {
        const response = await fetch('/api/chat/send/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message,
            project_id: options?.projectId,
            display_message: options?.displayMessage,
            agent_id: options?.agentId,
            agent_name: options?.agentName,
            mode: options?.mode || 'agent',
            system_prompt: options?.systemPrompt,
            model: options?.model,
            session_id: options?.sessionId,
            skill_ids: options?.skillIds,
            deep_thinking: options?.deepThinking,
            sandbox: options?.sandbox,
            images: options?.images,
            files: options?.files,
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const payload = await response.json();
        const runId = String(payload?.run?.id || payload?.runId || '');
        if (!runId) {
          throw new Error('Run was not started');
        }
        await subscribeRun(runId);
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          // 用户主动中止
          setState((prev) => completeStreamIfNoPendingUserInteraction(prev));
        } else {
          // 其他错误
          const errorMessage = err instanceof Error ? err.message : String(err);
          setState((prev) => failStreamWithError(prev, errorMessage));
        }
      }
    },
    [subscribeRun]
  );

  /** 恢复暂停的人机交互 */
  const resumeInteraction = useCallback(
    async (
      interactionId: string,
      action: 'approve' | 'reject',
      externalResults: Record<string, string> = {},
      rememberApproval = false,
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
        clarificationRequests: prev.clarificationRequests.filter((item) => item.id !== interactionId),
      }));

      try {
        const response = await fetch('/api/chat/resume/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            interactionId,
            action,
            externalResults,
            rememberApproval,
          }),
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        const payload = await response.json();
        const runId = String(payload?.runId || '');
        if (!runId) {
          throw new Error('Run was not resumed');
        }
        await subscribeRun(runId);
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
    [subscribeRun]
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

  return { state, sendMessage, resumeInteraction, subscribeRun, abort, reset };
}
