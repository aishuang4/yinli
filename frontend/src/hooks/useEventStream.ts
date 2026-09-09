/** Chat 的 RuntimeFrame V3 / DisplaySnapshot V4 传输 Hook。 */

import { useCallback, useRef, useState } from 'react';
import { coreBlockRegistry } from '../features/runtime-stream/coreModules';
import { RuntimeProtocolError } from '../features/runtime-stream/protocol';
import {
  acceptRuntimeFrame,
  checkRuntimeGapTimeout,
  createRuntimeStreamState,
} from '../features/runtime-stream/streamAdapter';
import { useAppStore } from '../stores/appStore';
import type {
  SendMessageOptions,
  SSEEvent,
  StreamState,
  UseEventStreamReturn,
} from './eventStreamTypes';

export type { DisplaySnapshot } from '../features/runtime-stream/protocol';
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

function initialState(): StreamState {
  return {
    isStreaming: false,
    resolvingInteractionId: '',
    currentRunId: '',
    runStartedAt: '',
    runCompletedAt: '',
    runtime: createRuntimeStreamState(),
    error: null,
  };
}

export function useEventStream(): UseEventStreamReturn {
  const activeProjectId = useAppStore((storeState) => storeState.activeProjectId);
  const [state, setReactState] = useState<StreamState>(() => initialState());
  const stateRef = useRef(state);
  const abortControllerRef = useRef<AbortController | null>(null);
  const activeSubscriptionRunIdRef = useRef('');

  const setState = useCallback((updater: (current: StreamState) => StreamState) => {
    const next = updater(stateRef.current);
    stateRef.current = next;
    setReactState(next);
  }, []);

  const handleEvent = useCallback((event: SSEEvent) => {
    if (event.type !== 'RUNTIME_EVENT') {
      throw new RuntimeProtocolError(`不支持的 SSE 应用事件: ${event.type}`);
    }
    setState((current) => {
      const runtime = acceptRuntimeFrame(current.runtime, event.data, coreBlockRegistry);
      return {
        ...current,
        runtime,
        isStreaming: runtime.snapshot.streamDone ? false : current.isStreaming,
        runCompletedAt: runtime.snapshot.streamDone
          ? current.runCompletedAt || new Date().toISOString()
          : current.runCompletedAt,
      };
    });
  }, [setState]);

  const readEventStream = useCallback(async (response: Response): Promise<'ended' | 'snapshot_required'> => {
    if (!response.body) throw new Error('Response body is null');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replaceAll('\r\n', '\n');
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() || '';
      for (const chunk of chunks) {
        const parsed = parseSSEEvent(chunk);
        if (parsed) handleEvent(parsed);
      }
      setState((current) => {
        const runtime = checkRuntimeGapTimeout(current.runtime);
        return runtime === current.runtime ? current : { ...current, runtime };
      });
      if (stateRef.current.runtime.needsSnapshot) {
        await reader.cancel();
        return 'snapshot_required';
      }
    }
    const trailing = parseSSEEvent(buffer);
    if (trailing) handleEvent(trailing);
    return stateRef.current.runtime.needsSnapshot ? 'snapshot_required' : 'ended';
  }, [handleEvent, setState]);

  const reset = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    activeSubscriptionRunIdRef.current = '';
    const next = initialState();
    stateRef.current = next;
    setReactState(next);
  }, []);

  const abort = useCallback(async () => {
    const runId = stateRef.current.currentRunId;
    if (runId) {
      try {
        const params = new URLSearchParams({ projectId: activeProjectId });
        const response = await fetch(
          `/api/chat/runs/${encodeURIComponent(runId)}/cancel?${params.toString()}`,
          { method: 'POST' },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        // 保持现有订阅，等待后端 cancelled close Frame 与 done 收口全部 Block。
        return;
      } catch (error) {
        console.warn('[RuntimeStream] cancel failed:', error);
      }
    }
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    activeSubscriptionRunIdRef.current = '';
    setState((current) => ({
      ...current,
      isStreaming: false,
      error: current.error || '用户已停止本轮执行。',
      runCompletedAt: current.runCompletedAt || new Date().toISOString(),
    }));
  }, [activeProjectId, setState]);

  const subscribeRun = useCallback(async (runId: string) => {
    if (!runId) return;
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    activeSubscriptionRunIdRef.current = runId;
    const current = stateRef.current;
    const sameRun = current.currentRunId === runId;
    const afterSeq = sameRun ? current.runtime.snapshot.lastSeq : 0;
    setState((value) => ({
      ...(sameRun ? value : initialState()),
      currentRunId: runId,
      runStartedAt: sameRun ? value.runStartedAt : new Date().toISOString(),
      isStreaming: true,
      error: null,
    }));

    try {
      let forceSnapshot = false;
      while (true) {
        const params = new URLSearchParams({ projectId: activeProjectId });
        if (!forceSnapshot) params.set('afterSeq', String(afterSeq));
        const response = await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/stream?${params.toString()}`, {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        const result = await readEventStream(response);
        if (result !== 'snapshot_required') break;
        forceSnapshot = true;
      }
      setState((value) => ({
        ...value,
        isStreaming: false,
        runCompletedAt: value.runtime.snapshot.streamDone
          ? value.runCompletedAt || new Date().toISOString()
          : value.runCompletedAt,
      }));
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      const message = error instanceof Error ? error.message : String(error);
      setState((value) => ({
        ...value,
        isStreaming: false,
        error: message,
        runtime: { ...value.runtime, connection: 'protocol_error' },
      }));
    } finally {
      if (abortControllerRef.current === controller) {
        activeSubscriptionRunIdRef.current = '';
      }
    }
  }, [activeProjectId, readEventStream, setState]);

  const sendMessage = useCallback(async (message: string, options?: SendMessageOptions) => {
    abortControllerRef.current?.abort();
    const next = initialState();
    next.isStreaming = true;
    next.runStartedAt = new Date().toISOString();
    stateRef.current = next;
    setReactState(next);
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
          modelConfigId: options?.modelConfigId,
          session_id: options?.sessionId,
          skill_ids: options?.skillIds,
          deep_thinking: options?.deepThinking,
          plan_id: options?.planId,
          plan_action: options?.planAction,
          plan_feedback: options?.planFeedback,
          plan_requested: options?.planRequested,
          sandbox: options?.sandbox,
          images: options?.images,
          files: options?.files,
        }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      const payload = await response.json();
      const runId = String(payload?.run?.id || payload?.runId || '');
      if (!runId) throw new Error('Run was not started');
      await subscribeRun(runId);
    } catch (error) {
      const messageText = error instanceof Error ? error.message : String(error);
      setState((value) => ({ ...value, isStreaming: false, error: messageText }));
    }
  }, [setState, subscribeRun]);

  const resumeInteraction = useCallback(async (
    interactionId: string,
    action: 'approve' | 'reject',
    externalResults: Record<string, string> = {},
    rememberApproval = false,
  ) => {
    setState((value) => ({
      ...value,
      isStreaming: true,
      resolvingInteractionId: interactionId,
      error: null,
    }));
    try {
      const response = await fetch('/api/chat/resume/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interactionId, action, externalResults, rememberApproval }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      const payload = await response.json();
      const runId = String(payload?.runId || '');
      if (!runId) throw new Error('Run was not resumed');
      if (activeSubscriptionRunIdRef.current !== runId) {
        void subscribeRun(runId);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setState((value) => ({
        ...value,
        isStreaming: Boolean(activeSubscriptionRunIdRef.current),
        error: message,
      }));
    } finally {
      setState((value) => ({
        ...value,
        resolvingInteractionId: value.resolvingInteractionId === interactionId
          ? ''
          : value.resolvingInteractionId,
      }));
    }
  }, [setState, subscribeRun]);

  return { state, sendMessage, resumeInteraction, subscribeRun, abort, reset };
}

function parseSSEEvent(raw: string): SSEEvent | null {
  let eventType = '';
  const dataLines: string[] = [];
  for (const line of raw.split('\n')) {
    if (line.startsWith(':')) continue;
    if (line.startsWith('event:')) eventType = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  }
  if (!eventType || dataLines.length === 0) return null;
  const value: unknown = JSON.parse(dataLines.join('\n'));
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new RuntimeProtocolError('SSE data 必须为 RuntimeFrame 对象');
  }
  return { type: eventType, data: value as Record<string, unknown> };
}
