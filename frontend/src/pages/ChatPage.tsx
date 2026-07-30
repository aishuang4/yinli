/**
 * ChatPage — 对话页面（对接 AgentScope 2.0 真实 Agent）
 *
 * 用户打开应用的第一界面。包含：
 * - Agent 信息头（顶部）
 * - 消息列表（中间，可滚动）：用户消息 + Agent 流式回复
 * - 输入框（底部，固定）
 *
 * 使用 CSS Grid 三段式布局：auto 1fr auto
 * 通过 useEventStream hook 订阅真实 SSE 事件流
 *
 * @see 前端设计文档 §8.1 Chat 对话流程
 */

import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Bot, ChevronDown, MessageSquare, PanelLeftOpen, Plus, Settings } from 'lucide-react';
import { Tag, Tooltip } from '@/components/design';
import { useEventStream } from '../hooks/useEventStream';
import { useAgents } from '../hooks/useAgents';
import { useProjectResources } from '../hooks/useProjectResources';
import { useWorkspaceProjects } from '../hooks/useWorkspaceProjects';
import { useAppStore } from '../stores/appStore';
import { createId } from '../utils/id';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';
import { MessageBubble, StreamingBubble } from '../features/chat/ChatMessageList';
import { ChatStartScenarios } from '../features/chat/ChatStartScenarios';
import { AskUserComposer } from '../features/chat/AskUserComposer';
import { ChatComposer } from '../features/chat/ChatComposer';
import { PlanReviewComposer } from '../features/chat/PlanReviewComposer';
import { PlanFloatingPanel } from '../features/chat/PlanFloatingPanel';
import { ChatHistoryPanel } from '../features/chat/ChatHistoryPanel';
import { selectLatestPlan, selectPendingPlanReview, type PlanReviewData } from '../features/chat/planReviewUtils';
import { selectPendingAskUserInteractions } from '../features/chat/humanInteractionUtils';
import type { ChatFilePayload, ChatSession, LocalMessage, StoredChatMessage } from '../features/chat/types';
import { runtimeError, runtimeText } from '../features/runtime-stream/selectors';
import { useChatScroll } from '../features/chat/useChatScroll';
import { useChatAttachments } from '../features/chat/useChatAttachments';

const HISTORY_PANEL_TRANSITION_MS = 260;

export function ChatPage() {
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [targetMode, setTargetMode] = useState<'agent' | 'team'>('agent');
  const [selectedAgentId, setSelectedAgentId] = useState<string>('default');
  const [inputValue, setInputValue] = useState('');
  const [deepThinking, setDeepThinking] = useState(false);
  const [planMode, setPlanMode] = useState(false);
  const [planPanelOpen, setPlanPanelOpen] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [historyPanelMounted, setHistoryPanelMounted] = useState(true);
  const setView = useAppStore((state) => state.setView);
  const activeWorkspaceId = useAppStore((state) => state.activeWorkspaceId);
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const historyTransitionTimerRef = useRef<number | null>(null);
  const historyExpandFrameRef = useRef<number | null>(null);
  const messageInputRef = useRef<HTMLTextAreaElement>(null);
  const isComposingRef = useRef(false);
  const recoveredRunRef = useRef<string>('');
  const sessionsRequestRef = useRef(0);
  const messagesRequestRef = useRef(0);
  const pendingSessionRef = useRef<ChatSession | null>(null);
  const { state, sendMessage, resumeInteraction, subscribeRun, abort, reset } = useEventStream();
  const { agents, loading: agentsLoading } = useAgents();
  const { enabledByType, loading: projectResourcesLoading } = useProjectResources(activeProjectId);
  const { workspaces, allProjects } = useWorkspaceProjects();
  const setActiveContext = useAppStore((state) => state.setActiveContext);
  const {
    fileAttachments,
    handleFile,
    handleImageFiles,
    imageAttachments,
    imageInputRef,
    removeFileAttachment,
    removeImageAttachment,
    resetAttachments,
    uploadFilesForSession,
  } = useChatAttachments({ onError: setChatError });

  const availableAgents = useMemo(() => agents.filter((agent) => (
    agent.id === 'default'
    || (agent.workspaceId === activeWorkspaceId && enabledByType.agent.has(agent.id))
  )), [activeWorkspaceId, agents, enabledByType]);

  const selectedAgent = useMemo(
    () => availableAgents.find((agent) => agent.id === selectedAgentId) || null,
    [availableAgents, selectedAgentId]
  );

  const runtimeAgent = selectedAgent;

  const agentOptions = useMemo(() => availableAgents.map((agent) => ({
      label: agent.id === 'default' ? `${agent.name} - 默认助手` : agent.name,
      value: agent.id,
    })), [availableAgents]);

  const selectedModelId = runtimeAgent?.modelConfig.primary || 'deepseek-v4-flash';
  const selectedModelSupportsImages = selectedModelId === 'qwen3.7-plus';
  const isDesktopShell = Boolean(window.electronAPI);
  const desktopPlatform = window.electronAPI?.getPlatform();
  const isMacDesktopShell = desktopPlatform === 'darwin';
  const isWindowsDesktopShell = desktopPlatform === 'win32';
  const snapshot = state.runtime.snapshot;
  const {
    cancelProgrammaticScroll,
    handleMessagesScroll,
    handleMessagesWheel,
    messagesContainerRef,
    messagesEndRef,
    resetScroll,
    scrollToBottom,
    showScrollToBottom,
  } = useChatScroll({
    contentVersion: `${messages.length}:${snapshot.lastSeq}`,
    isStreaming: state.isStreaming,
  });
  const liveText = runtimeText(snapshot);
  const projectedError = runtimeError(snapshot);
  const pendingAskUserInteractions = useMemo(
    () => selectPendingAskUserInteractions(snapshot),
    [snapshot],
  );
  const activeAskUserInteraction = pendingAskUserInteractions[0];
  const pendingPlanReview = useMemo(
    () => selectPendingPlanReview(messages.map((message) => message.displaySnapshot), snapshot),
    [messages, snapshot],
  );
  const latestPlan = useMemo(
    () => selectLatestPlan(messages.map((message) => message.displaySnapshot), snapshot),
    [messages, snapshot],
  );
  const planIdentity = latestPlan ? `${latestPlan.planId}:${latestPlan.revision}` : '';
  const openedPlanIdentityRef = useRef('');
  useEffect(() => {
    if (!planIdentity || openedPlanIdentityRef.current === planIdentity) return;
    openedPlanIdentityRef.current = planIdentity;
    setPlanPanelOpen(true);
  }, [planIdentity]);
  const hasLiveResponse = state.isStreaming || snapshot.order.length > 0 || Boolean(state.error);
  const activeSession = useMemo(() => (
    sessions.find((session) => session.id === activeSessionId) || null
  ), [activeSessionId, sessions]);
  const handleCollapseHistory = useCallback(() => {
    if (historyTransitionTimerRef.current !== null) {
      window.clearTimeout(historyTransitionTimerRef.current);
    }
    if (historyExpandFrameRef.current !== null) {
      window.cancelAnimationFrame(historyExpandFrameRef.current);
      historyExpandFrameRef.current = null;
    }

    setHistoryCollapsed(true);
    historyTransitionTimerRef.current = window.setTimeout(() => {
      setHistoryPanelMounted(false);
      historyTransitionTimerRef.current = null;
    }, HISTORY_PANEL_TRANSITION_MS);
  }, []);
  const handleExpandHistory = useCallback(() => {
    if (historyTransitionTimerRef.current !== null) {
      window.clearTimeout(historyTransitionTimerRef.current);
      historyTransitionTimerRef.current = null;
    }
    if (historyExpandFrameRef.current !== null) {
      window.cancelAnimationFrame(historyExpandFrameRef.current);
    }

    setHistoryPanelMounted(true);
    setHistoryCollapsed(true);
    historyExpandFrameRef.current = window.requestAnimationFrame(() => {
      historyExpandFrameRef.current = null;
      setHistoryCollapsed(false);
    });
  }, []);
  useEffect(() => () => {
    if (historyTransitionTimerRef.current !== null) {
      window.clearTimeout(historyTransitionTimerRef.current);
    }
    if (historyExpandFrameRef.current !== null) {
      window.cancelAnimationFrame(historyExpandFrameRef.current);
    }
  }, []);

  const loadSessions = useCallback(async (options?: { silent?: boolean }) => {
    const silent = Boolean(options?.silent);
    const requestId = sessionsRequestRef.current + 1;
    sessionsRequestRef.current = requestId;
    try {
      if (!silent) {
        setSessionsLoading(true);
      }
      const response = await fetch('/api/chat/sessions/catalog');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data: ChatSession[] = await response.json();
      if (sessionsRequestRef.current === requestId) setSessions(data);
    } finally {
      if (!silent) {
        setSessionsLoading(false);
      }
    }
  }, []);

  const loadSessionMessages = useCallback(async (sessionId: string, projectId = activeProjectId) => {
    const requestId = messagesRequestRef.current + 1;
    messagesRequestRef.current = requestId;
    const params = new URLSearchParams({ projectId });
    const response = await fetch(`/api/chat/sessions/${sessionId}/messages?${params.toString()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data: StoredChatMessage[] = await response.json();
    const loadedMessages = data.map((item) => ({
      id: item.id,
      role: item.role,
      content: item.content,
      createdAt: item.created_at,
      modelName: item.model_name || undefined,
      error: item.error || undefined,
      thinking: item.thinking || undefined,
      images: item.images,
      files: item.files,
      displaySnapshot: item.displaySnapshot || item.display_snapshot,
      clarificationRequests: item.clarificationRequests || item.clarification_requests || [],
      clarificationResolved: item.clarificationResolved || item.clarification_resolved || false,
    }));
    if (messagesRequestRef.current === requestId) {
      setMessages(loadedMessages);
    }
  }, [activeProjectId]);

  const activateSession = useCallback(async (session: ChatSession) => {
    recoveredRunRef.current = '';
    openedPlanIdentityRef.current = '';
    setPlanPanelOpen(false);
    setMessages([]);
    reset();
    setActiveSessionId(session.id);
    setTargetMode(session.mode || 'agent');
    setSelectedAgentId(session.agent_id || 'default');
    resetAttachments();
    setChatError(null);
    resetScroll();
    await loadSessionMessages(session.id, session.project_id);
  }, [loadSessionMessages, reset, resetAttachments, resetScroll]);

  useEffect(() => {
    sessionsRequestRef.current += 1;
    messagesRequestRef.current += 1;
    recoveredRunRef.current = '';
    openedPlanIdentityRef.current = '';
    setPlanPanelOpen(false);
    setActiveSessionId(null);
    setMessages([]);
    resetAttachments();
    setChatError(null);
    reset();
    const pendingSession = pendingSessionRef.current;
    if (
      pendingSession &&
      pendingSession.workspace_id === activeWorkspaceId &&
      pendingSession.project_id === activeProjectId
    ) {
      pendingSessionRef.current = null;
      void activateSession(pendingSession);
    }
  }, [activeProjectId, activeWorkspaceId, activateSession, reset, resetAttachments]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    if (selectedAgentId !== 'default' && !availableAgents.some((agent) => agent.id === selectedAgentId)) {
      setSelectedAgentId('default');
    }
  }, [availableAgents, selectedAgentId]);

  useEffect(() => {
    if (!activeSessionId || hasLiveResponse) return;
    let cancelled = false;

    async function recoverActiveRun() {
      try {
        const params = new URLSearchParams({ projectId: activeProjectId });
        const response = await fetch(`/api/chat/runs?${params.toString()}`);
        if (!response.ok) return;
        const data = await response.json();
        const runs = Array.isArray(data.runs) ? data.runs : [];
        const activeRun = runs.find((run: Record<string, unknown>) => {
          const sessionId = String(run.sessionId || run.session_id || '');
          const status = String(run.status || '');
          return (
            sessionId === activeSessionId &&
            (
              status === 'running' ||
              status === 'waiting' ||
              status === 'waiting_user' ||
              status === 'waiting_permission' ||
              status === 'waiting_external' ||
              status === 'cancelling'
            )
          );
        });
        const runId = String(activeRun?.id || '');
        if (!runId || cancelled || recoveredRunRef.current === runId) return;
        recoveredRunRef.current = runId;
        await subscribeRun(runId);
      } catch (error) {
        console.warn('[Chat] Failed to recover active run:', error);
      }
    }

    void recoverActiveRun();
    return () => {
      cancelled = true;
    };
  }, [activeProjectId, activeSessionId, hasLiveResponse, subscribeRun]);

  const handleNewSession = useCallback(() => {
    if (state.isStreaming) return;
    pendingSessionRef.current = null;
    recoveredRunRef.current = '';
    openedPlanIdentityRef.current = '';
    setPlanPanelOpen(false);
    setActiveSessionId(null);
    setMessages([]);
    resetAttachments();
    setChatError(null);
    resetScroll();
    reset();
  }, [reset, resetAttachments, resetScroll, state.isStreaming]);

  const handleScenarioSelect = useCallback((prompt: string) => {
    setInputValue(prompt);
    window.requestAnimationFrame(() => messageInputRef.current?.focus());
  }, []);

  const handleSelectSession = useCallback(async (session: ChatSession) => {
    if (state.isStreaming) return;
    if (session.project_id !== activeProjectId || session.workspace_id !== activeWorkspaceId) {
      pendingSessionRef.current = session;
      setActiveContext(session.workspace_id, session.project_id);
      return;
    }
    await activateSession(session);
  }, [activeProjectId, activeWorkspaceId, activateSession, setActiveContext, state.isStreaming]);

  const handleDeleteSession = useCallback(async (session: ChatSession) => {
    if (state.isStreaming) return;
    try {
      const params = new URLSearchParams({ projectId: session.project_id });
      const response = await fetch(`/api/chat/sessions/${session.id}?${params.toString()}`, { method: 'DELETE' });
      if (!response.ok && response.status !== 404) throw new Error(`HTTP ${response.status}`);
      setSessions((current) => current.filter((item) => item.id !== session.id));
      if (activeSessionId === session.id) {
        recoveredRunRef.current = '';
        openedPlanIdentityRef.current = '';
        setPlanPanelOpen(false);
        setActiveSessionId(null);
        setMessages([]);
        resetAttachments();
        setChatError(null);
        reset();
      }
      notifySuccess('历史对话已删除');
    } catch (err) {
      notifyError(`删除历史对话失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [activeSessionId, reset, resetAttachments, state.isStreaming]);

  /** 发送消息 */
  const handleSend = useCallback(async () => {
    if ((!inputValue.trim() && imageAttachments.length === 0 && fileAttachments.length === 0) || state.isStreaming) return;
    if (!activeProjectId) {
      setChatError('请先在设置中选择 Workspace 和 Project。');
      return;
    }
    if (imageAttachments.length > 0 && !selectedModelSupportsImages) {
      setChatError(`${selectedModelId} 不支持图片输入，请选择使用 qwen3.7-plus 的 Agent。`);
      return;
    }

    const userText = inputValue.trim() || (fileAttachments.length > 0 ? '请处理我上传的文件。' : '请分析这张图片。');
    const sessionId = activeSessionId || createId();
    const images = imageAttachments.map(({ name, media_type, data }) => ({
      name,
      media_type,
      data,
    }));
    let files: ChatFilePayload[];
    try {
      files = await uploadFilesForSession(fileAttachments, activeProjectId, sessionId);
    } catch (err) {
      setChatError(getErrorMessage(err, '附件上传失败。'));
      return;
    }
    const userMessage: LocalMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: userText,
      createdAt: new Date().toISOString(),
      images,
      files,
    };

    setActiveSessionId(sessionId);
    resetScroll();
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    resetAttachments();
    setChatError(null);

    await sendMessage(userText, {
      projectId: activeProjectId,
      sessionId,
      agentId: runtimeAgent?.id,
      agentName: runtimeAgent?.name,
      mode: targetMode,
      systemPrompt: runtimeAgent?.systemPrompt,
      model: runtimeAgent?.modelConfig.primary,
      skillIds: runtimeAgent?.skills,
      deepThinking,
      planRequested: planMode && targetMode === 'agent',
      images,
      files,
    });
  }, [activeProjectId, activeSessionId, deepThinking, fileAttachments, imageAttachments, inputValue, planMode, resetAttachments, resetScroll, runtimeAgent, selectedModelId, selectedModelSupportsImages, sendMessage, state.isStreaming, targetMode, uploadFilesForSession]);

  const handlePlanReview = useCallback(async (
    plan: PlanReviewData,
    action: 'revise' | 'approve' | 'cancel',
    feedback = '',
  ) => {
    if (!activeSessionId || state.isStreaming) return;
    const userText = action === 'approve'
      ? `确认执行计划 Revision ${plan.revision}`
      : action === 'cancel'
        ? `取消计划 Revision ${plan.revision}`
        : `修改计划：${feedback}`;
    setMessages((current) => [...current, {
      id: `user-plan-${Date.now()}`,
      role: 'user',
      content: userText,
      createdAt: new Date().toISOString(),
    }]);
    resetScroll();
    setChatError(null);
    await sendMessage(userText, {
      projectId: activeProjectId,
      sessionId: activeSessionId,
      agentId: runtimeAgent?.id,
      agentName: runtimeAgent?.name,
      mode: 'agent',
      systemPrompt: runtimeAgent?.systemPrompt,
      model: runtimeAgent?.modelConfig.primary,
      skillIds: runtimeAgent?.skills,
      deepThinking,
      planId: plan.planId,
      planAction: action,
      planFeedback: feedback,
    });
  }, [activeProjectId, activeSessionId, deepThinking, resetScroll, runtimeAgent, sendMessage, state.isStreaming]);

  // 当流结束时，将 assistant 回复加入消息历史
  useEffect(() => {
    if (
      !state.isStreaming &&
      snapshot.streamDone &&
      snapshot.order.length > 0 &&
      (messages.length === 0 || messages[messages.length - 1].role === 'user')
    ) {
      const assistantMessage: LocalMessage = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: liveText,
        createdAt: new Date().toISOString(),
        processStartedAt: state.runStartedAt,
        processCompletedAt: state.runCompletedAt || new Date().toISOString(),
        displaySnapshot: snapshot,
        error: state.error || projectedError || undefined,
      };
      setMessages((prev) => [...prev, assistantMessage]);
      reset();
      loadSessions({ silent: true });
    }
  }, [liveText, loadSessions, messages, projectedError, reset, snapshot, state.error, state.isStreaming, state.runCompletedAt, state.runStartedAt]);

  /** 回车发送 */
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !isComposingRef.current) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  return (
    <div
      className="chat-page-shell chat-workbench-shell grid h-full min-h-0 overflow-hidden"
      data-history-mounted={historyPanelMounted}
      data-history-collapsed={historyCollapsed}
      data-electron-shell={isDesktopShell}
      data-windows-shell={isWindowsDesktopShell}
      style={{
        gridTemplateColumns: historyPanelMounted
          ? historyCollapsed
            ? '0px minmax(0, 1fr)'
            : '286px minmax(0, 1fr)'
          : 'minmax(0, 1fr)',
        transition: `grid-template-columns ${HISTORY_PANEL_TRANSITION_MS}ms var(--ease-out)`,
      }}
    >
      {historyPanelMounted && (
        <ChatHistoryPanel
          activeSessionId={activeSessionId}
          allProjects={allProjects}
          collapsed={historyCollapsed}
          isMacDesktopShell={isMacDesktopShell}
          isStreaming={state.isStreaming}
          loading={sessionsLoading}
          sessions={sessions}
          workspaces={workspaces}
          onCollapse={handleCollapseHistory}
          onDeleteSession={(session) => { void handleDeleteSession(session); }}
          onNewSession={handleNewSession}
          onSelectSession={(session) => { void handleSelectSession(session); }}
        />
      )}

      <section
        className="chat-main-panel chat-workbench-main grid min-h-0 grid-cols-1 overflow-hidden"
      >
        <div className="grid min-h-0 grid-rows-[auto_1fr_auto] overflow-hidden">
          <header
            className="chat-workbench-topbar flex items-center justify-between gap-4 px-8 py-2"
            style={isMacDesktopShell && (!historyPanelMounted || historyCollapsed) ? { paddingLeft: 128 } : undefined}
          >
          <div className="flex min-w-0 items-center gap-3">
            {historyCollapsed && (
              <>
                <Tooltip title="展开历史对话" placement="bottom" mode="dark">
                  <button
                    type="button"
                    className="icon-button chat-history-collapse-button"
                    onClick={handleExpandHistory}
                    aria-label="展开历史对话"
                  >
                    <PanelLeftOpen size={17} strokeWidth={1.7} />
                  </button>
                </Tooltip>
                <Tooltip title="新建会话" placement="bottom" mode="dark">
                  <button
                    type="button"
                    className="icon-button chat-history-new-button"
                    onClick={handleNewSession}
                    disabled={state.isStreaming}
                    aria-label="新建会话"
                  >
                    <Plus size={15} />
                  </button>
                </Tooltip>
              </>
            )}
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)]">
              <MessageSquare size={17} strokeWidth={1.7} />
            </div>
            <div className="min-w-0">
              <div className="truncate text-base font-semibold text-[var(--color-text-primary)]">
                {activeSession?.title || '新对话'}
              </div>
              <div className="truncate text-xs text-[var(--color-text-tertiary)]">
                {state.isStreaming
                  ? runtimeAgent?.modelConfig.primary || 'deepseek-v4-flash'
                  : targetMode === 'team'
                    ? `${runtimeAgent?.name || 'Assistant'} · Leader 动态组建临时成员`
                    : `${runtimeAgent?.name || 'Assistant'} · ${runtimeAgent?.description || 'DeepSeek'}`}
              </div>
            </div>
            {activeSessionId && (
              <Tag className="chat-workbench-status-tag" size="small" color="transparent">
                已保存
              </Tag>
            )}
            {selectedModelSupportsImages && (
              <Tag className="chat-workbench-status-tag chat-workbench-status-tag--blue" size="small" color="blue">
                图片
              </Tag>
            )}
            {targetMode === 'team' && (
              <Tag className="chat-workbench-status-tag chat-workbench-status-tag--mauve" size="small" color="mauve">
                Team
              </Tag>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Tooltip title="工作台设置" placement="bottom" mode="dark">
              <button
                type="button"
                className="icon-button chat-workbench-top-action h-9 w-9 rounded-xl"
                onClick={() => setView('settings')}
                aria-label="进入工作台设置"
              >
                <Settings size={17} />
              </button>
            </Tooltip>
          </div>
          </header>

          <div className="relative min-h-0 overflow-hidden">
            <div
              ref={messagesContainerRef}
              className="h-full min-h-0 overflow-auto px-8 py-4"
              onScroll={handleMessagesScroll}
              onWheel={handleMessagesWheel}
              onPointerDown={cancelProgrammaticScroll}
              role="log"
              aria-live="polite"
              aria-label="对话消息"
            >
              <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-4">
                {messages.length === 0 && !hasLiveResponse ? (
                  <div className="chat-empty-state flex flex-1 flex-col items-center justify-center gap-3 text-center text-[var(--color-text-tertiary)]">
                    <div className="chat-empty-state__icon">
                      <Bot size={42} strokeWidth={1.2} />
                    </div>
                    <div className="text-lg font-medium">开始对话</div>
                    <div className="max-w-[420px] text-sm">
                      选择一个场景开始，或直接在下方输入你的任务。
                    </div>
                    <ChatStartScenarios onSelect={handleScenarioSelect} />
                  </div>
                ) : (
                  <>
                    {messages.map((msg) => (
                      <MessageBubble
                        key={msg.id}
                        message={msg}
                        sessionId={activeSessionId || undefined}
                      />
                    ))}

                    {hasLiveResponse && (
                      <StreamingBubble
                        snapshot={snapshot}
                        isStreaming={state.isStreaming}
                        modelName={runtimeAgent?.modelConfig.primary || ''}
                        error={state.error}
                        createdAt={state.runCompletedAt || undefined}
                        sessionId={activeSessionId || undefined}
                        onResolveInteraction={resumeInteraction}
                      />
                    )}

                    <div ref={messagesEndRef} />
                  </>
                )}
              </div>
            </div>
            <button
              type="button"
              className={`icon-button absolute bottom-4 left-1/2 h-9 w-9 -translate-x-1/2 rounded-full border border-[var(--color-border)] bg-[var(--color-bg-primary)]/70 p-0 text-[var(--color-text-secondary)] shadow-sm backdrop-blur transition-all duration-300 ease-out hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)] ${
                showScrollToBottom
                  ? 'pointer-events-auto opacity-100'
                  : 'pointer-events-none translate-y-2 opacity-0'
              }`}
              onClick={() => scrollToBottom('smooth')}
              aria-label="滚动到底部"
              aria-hidden={!showScrollToBottom}
            >
              <ChevronDown size={16} />
            </button>
            {latestPlan ? (
              <PlanFloatingPanel
                key={latestPlan.planId}
                plan={latestPlan}
                open={planPanelOpen}
                onOpenChange={setPlanPanelOpen}
              />
            ) : null}
          </div>

          {activeAskUserInteraction ? (
            <AskUserComposer
              key={activeAskUserInteraction.hitlRequest.interactionId}
              interaction={activeAskUserInteraction}
              pendingCount={pendingAskUserInteractions.length}
              isResuming={state.isStreaming}
              onResolve={resumeInteraction}
            />
          ) : pendingPlanReview ? (
            <PlanReviewComposer
              key={`${pendingPlanReview.planId}:${pendingPlanReview.revision}`}
              plan={pendingPlanReview}
              submitting={state.isStreaming}
              onSubmit={(action, feedback) => handlePlanReview(pendingPlanReview, action, feedback)}
            />
          ) : (
            <ChatComposer
              agentOptions={agentOptions}
              agentsLoading={agentsLoading}
              chatError={chatError}
              deepThinking={deepThinking}
              fileAttachments={fileAttachments}
              imageAttachments={imageAttachments}
              imageInputRef={imageInputRef}
              inputValue={inputValue}
              isStreaming={state.isStreaming}
              messageInputRef={messageInputRef}
              planMode={planMode}
              projectResourcesLoading={projectResourcesLoading}
              selectedAgentId={selectedAgentId}
              targetMode={targetMode}
              onAbort={abort}
              onCompositionEnd={() => { isComposingRef.current = false; }}
              onCompositionStart={() => { isComposingRef.current = true; }}
              onFile={handleFile}
              onImageFiles={handleImageFiles}
              onInputChange={setInputValue}
              onKeyDown={handleKeyDown}
              onRemoveFile={removeFileAttachment}
              onRemoveImage={removeImageAttachment}
              onSelectedAgentChange={setSelectedAgentId}
              onSend={handleSend}
              onTargetModeChange={setTargetMode}
              onToggleDeepThinking={() => setDeepThinking((value) => !value)}
              onTogglePlanMode={() => setPlanMode((value) => !value)}
            />
          )}

        </div>
      </section>
    </div>
  );
}
