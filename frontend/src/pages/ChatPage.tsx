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

import { useState, useCallback, useRef, useEffect, useMemo, type WheelEvent } from 'react';
import { Bot, ChevronDown, MessageSquare, PanelLeftOpen, Plus, Settings } from 'lucide-react';
import { Tag, Tooltip } from '@agentscope-ai/design';
import { useEventStream } from '../hooks/useEventStream';
import { useAgents } from '../hooks/useAgents';
import { useProjectResources } from '../hooks/useProjectResources';
import { useWorkspaceProjects } from '../hooks/useWorkspaceProjects';
import { useAppStore } from '../stores/appStore';
import { createId } from '../utils/id';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';
import { MessageBubble, StreamingBubble } from '../features/chat/ChatMessageList';
import { TeamConsolePanel } from '../features/chat/TeamDisplayBlock';
import { teamConsoleFromSnapshot } from '../features/team-runtime/teamEventReducer';
import { FloatingPlanDock } from '../features/chat/FloatingPlanDock';
import { ChatStartScenarios } from '../features/chat/ChatStartScenarios';
import { ChatComposer } from '../features/chat/ChatComposer';
import { ChatHistoryPanel } from '../features/chat/ChatHistoryPanel';
import { mergeResolvedClarificationFeedbackIntoReplies } from '../features/chat/clarificationHistory';
import type { ChatFileAttachment, ChatFilePayload, ChatImageAttachment, ChatSession, LocalMessage, StoredChatMessage } from '../features/chat/types';

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
  const [imageAttachments, setImageAttachments] = useState<ChatImageAttachment[]>([]);
  const [fileAttachments, setFileAttachments] = useState<ChatFileAttachment[]>([]);
  const [chatError, setChatError] = useState<string | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [historyPanelMounted, setHistoryPanelMounted] = useState(true);
  const setView = useAppStore((state) => state.setView);
  const activeWorkspaceId = useAppStore((state) => state.activeWorkspaceId);
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const programmaticScrollRef = useRef(false);
  const programmaticScrollTimerRef = useRef<number | null>(null);
  const historyTransitionTimerRef = useRef<number | null>(null);
  const historyExpandFrameRef = useRef<number | null>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const messageInputRef = useRef<HTMLTextAreaElement>(null);
  const isComposingRef = useRef(false);
  const recoveredRunRef = useRef<string>('');
  const completedVisualRunRef = useRef<string>('');
  const sessionsRequestRef = useRef(0);
  const messagesRequestRef = useRef(0);
  const pendingSessionRef = useRef<ChatSession | null>(null);
  const [visuallyCompletedRunId, setVisuallyCompletedRunId] = useState('');
  const { state, sendMessage, resumeInteraction, subscribeRun, abort, reset } = useEventStream();
  const { agents, loading: agentsLoading } = useAgents();
  const { enabledByType, loading: projectResourcesLoading } = useProjectResources(activeProjectId);
  const { workspaces, allProjects } = useWorkspaceProjects();
  const setActiveContext = useAppStore((state) => state.setActiveContext);

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
  const hasLiveResponse = (
    state.isStreaming ||
    state.hitlRequests.length > 0 ||
    state.clarificationRequests.length > 0 ||
    Boolean(state.finalAnswerContent) ||
    Boolean(state.reasoningContent) ||
    Boolean(state.thinkingContent) ||
    state.toolCalls.length > 0 ||
    state.skillCalls.length > 0 ||
    Boolean(state.teamConsole.snapshot) ||
    state.displaySnapshot.blocks.length > 0 ||
    state.plans.length > 0
  );

  const historicalTeamSnapshot = useMemo(() => (
    [...messages].reverse().find((message) => message.role === 'assistant')?.teamSnapshot || null
  ), [messages]);
  const visibleTeamConsole = state.teamConsole.snapshot
    ? state.teamConsole
    : !state.isStreaming && historicalTeamSnapshot
      ? teamConsoleFromSnapshot(historicalTeamSnapshot)
      : null;
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

  const handleFinalAnswerAnimationChange = useCallback((isAnimating: boolean) => {
    const runId = state.currentRunId;
    if (!runId) return;
    if (isAnimating) {
      if (completedVisualRunRef.current === runId) {
        completedVisualRunRef.current = '';
        setVisuallyCompletedRunId('');
      }
      return;
    }
    if (completedVisualRunRef.current !== runId) {
      completedVisualRunRef.current = runId;
      setVisuallyCompletedRunId(runId);
    }
  }, [state.currentRunId]);

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
      teamSnapshot: item.teamSnapshot || item.team_snapshot,
      clarificationRequests: item.clarificationRequests || item.clarification_requests || [],
      clarificationResolved: item.clarificationResolved || item.clarification_resolved || false,
    }));
    if (messagesRequestRef.current === requestId) {
      setMessages(mergeResolvedClarificationFeedbackIntoReplies(loadedMessages));
    }
  }, [activeProjectId]);

  const activateSession = useCallback(async (session: ChatSession) => {
    recoveredRunRef.current = '';
    reset();
    setActiveSessionId(session.id);
    setTargetMode(session.mode || 'agent');
    setSelectedAgentId(session.agent_id || 'default');
    setImageAttachments([]);
    setFileAttachments([]);
    setChatError(null);
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    await loadSessionMessages(session.id, session.project_id);
  }, [loadSessionMessages, reset]);

  useEffect(() => {
    sessionsRequestRef.current += 1;
    messagesRequestRef.current += 1;
    recoveredRunRef.current = '';
    completedVisualRunRef.current = '';
    setVisuallyCompletedRunId('');
        setActiveSessionId(null);
        setMessages([]);
        setImageAttachments([]);
        setFileAttachments([]);
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
  }, [activeProjectId, activeWorkspaceId, activateSession, reset]);

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

  const handleMessagesScroll = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    const nearBottom = distanceToBottom < 80;
    if (programmaticScrollRef.current) {
      shouldAutoScrollRef.current = true;
      setShowScrollToBottom(false);
      if (nearBottom) {
        programmaticScrollRef.current = false;
      }
      return;
    }
    shouldAutoScrollRef.current = nearBottom;
    setShowScrollToBottom(!nearBottom);
  }, []);

  const cancelProgrammaticScroll = useCallback(() => {
    programmaticScrollRef.current = false;
    if (programmaticScrollTimerRef.current !== null) {
      window.clearTimeout(programmaticScrollTimerRef.current);
      programmaticScrollTimerRef.current = null;
    }
  }, []);

  const handleMessagesWheel = useCallback((event: WheelEvent<HTMLDivElement>) => {
    if (event.deltaY < 0) {
      cancelProgrammaticScroll();
    }
  }, [cancelProgrammaticScroll]);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    shouldAutoScrollRef.current = true;
    programmaticScrollRef.current = true;
    setShowScrollToBottom(false);
    const container = messagesContainerRef.current;
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior });
    } else {
      messagesEndRef.current?.scrollIntoView({ behavior });
    }
    if (programmaticScrollTimerRef.current !== null) {
      window.clearTimeout(programmaticScrollTimerRef.current);
    }
    programmaticScrollTimerRef.current = window.setTimeout(() => {
      programmaticScrollRef.current = false;
      programmaticScrollTimerRef.current = null;
    }, behavior === 'smooth' ? 700 : 80);
  }, []);

  /** 只有用户停留在底部附近时才自动滚动 */
  useEffect(() => {
    if (!shouldAutoScrollRef.current) return;
    scrollToBottom(state.isStreaming ? 'auto' : 'smooth');
  }, [messages, scrollToBottom, state.isStreaming, state.finalAnswerContent, state.reasoningContent, state.thinkingContent, state.toolCalls, state.skillCalls, state.teamConsole]);

  useEffect(() => {
    const container = messagesContainerRef.current;
    const content = container?.firstElementChild;
    if (!container || !content || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (!shouldAutoScrollRef.current) return;
      scrollToBottom('auto');
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [scrollToBottom]);

  useEffect(() => () => {
    if (programmaticScrollTimerRef.current !== null) {
      window.clearTimeout(programmaticScrollTimerRef.current);
    }
  }, []);

  const handleNewSession = useCallback(() => {
    if (state.isStreaming) return;
    pendingSessionRef.current = null;
    recoveredRunRef.current = '';
    setActiveSessionId(null);
    setMessages([]);
    setImageAttachments([]);
    setFileAttachments([]);
    setChatError(null);
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    reset();
  }, [reset, state.isStreaming]);

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
        setActiveSessionId(null);
        setMessages([]);
        setImageAttachments([]);
        setFileAttachments([]);
        setChatError(null);
        reset();
      }
      notifySuccess('历史对话已删除');
    } catch (err) {
      notifyError(`删除历史对话失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [activeSessionId, reset, state.isStreaming]);

  const handleImageFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;

    const imageFiles = Array.from(files).filter((file) => file.type.startsWith('image/'));
    const nextAttachments = await Promise.all(imageFiles.map((file) => (
      new Promise<ChatImageAttachment>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const previewUrl = String(reader.result);
          const [, data = ''] = previewUrl.split(',', 2);
          resolve({
            id: createId(),
            name: file.name,
            media_type: file.type || 'image/png',
            data,
            previewUrl,
          });
        };
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      })
    )));

    setImageAttachments((current) => [...current, ...nextAttachments].slice(0, 4));
    setChatError(null);
    if (imageInputRef.current) {
      imageInputRef.current.value = '';
    }
  }, []);

  const removeImageAttachment = useCallback((id: string) => {
    setImageAttachments((current) => current.filter((item) => item.id !== id));
  }, []);

  const handleFile = useCallback((file: File) => {
    if (file.size > 50 * 1024 * 1024) {
      setChatError('单个附件不能超过 50 MB。');
      return;
    }
    setFileAttachments((current) => {
      if (current.length >= 10) {
        setChatError('单次最多上传 10 个附件。');
        return current;
      }
      if (current.some((item) => item.name === file.name && item.size === file.size)) {
        return current;
      }
      return [...current, { id: createId(), file, name: file.name, size: file.size }];
    });
    setChatError(null);
  }, []);

  const removeFileAttachment = useCallback((id: string) => {
    setFileAttachments((current) => current.filter((item) => item.id !== id));
  }, []);

  const uploadFilesForSession = useCallback(async (
    files: ChatFileAttachment[],
    projectId: string,
    sessionId: string,
  ): Promise<ChatFilePayload[]> => {
    return Promise.all(files.map(async (attachment) => {
      const form = new FormData();
      form.append('file', attachment.file, attachment.name);
      const params = new URLSearchParams({ sessionId });
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/files?${params.toString()}`, {
        method: 'POST',
        body: form,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(String(payload.detail || `附件 ${attachment.name} 上传失败`));
      }
      return {
        name: String(payload.name || attachment.name),
        path: String(payload.path || ''),
        size: Number(payload.size || attachment.size),
        media_type: String(payload.mediaType || attachment.file.type || 'application/octet-stream'),
      };
    }));
  }, []);

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
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setImageAttachments([]);
    setFileAttachments([]);
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
      images,
      files,
    });
  }, [activeProjectId, activeSessionId, deepThinking, fileAttachments, imageAttachments, inputValue, runtimeAgent, selectedModelId, selectedModelSupportsImages, sendMessage, state.isStreaming, targetMode, uploadFilesForSession]);

  const handleSubmitClarification = useCallback(async (content: string, displayContent?: string) => {
    const modelContent = content.trim();
    if (!modelContent || state.isStreaming) return;
    const visibleContent = (displayContent || content).trim();
    const clarificationId = state.clarificationRequests[0]?.id;
    if (!clarificationId) {
      setChatError('当前没有可继续的反问请求。');
      return;
    }

    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    setChatError(null);
    await resumeInteraction(clarificationId, 'approve', {
      answer: modelContent,
      display: visibleContent,
    });
  }, [
    state.isStreaming,
    state.clarificationRequests,
    resumeInteraction,
  ]);

  // 当流结束时，将 assistant 回复加入消息历史
  useEffect(() => {
    const waitingForFinalAnswerAnimation = Boolean(
      state.currentRunId &&
      state.finalAnswerContent &&
      visuallyCompletedRunId !== state.currentRunId,
    );
    if (
      !state.isStreaming &&
      !waitingForFinalAnswerAnimation &&
      state.hitlRequests.length === 0 &&
      state.clarificationRequests.length === 0 &&
      (state.finalAnswerContent || state.reasoningContent || state.textContent || state.error || state.displaySnapshot.blocks.length > 0 || state.plans.length > 0) &&
      (messages.length === 0 || messages[messages.length - 1].role === 'user')
    ) {
      const assistantMessage: LocalMessage = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: state.finalAnswerContent || state.textContent,
        createdAt: new Date().toISOString(),
        processStartedAt: state.runStartedAt,
        processCompletedAt: state.runCompletedAt || new Date().toISOString(),
        displaySnapshot: state.displaySnapshot.blocks.length > 0 || state.displaySnapshot.actors.length > 0 ? state.displaySnapshot : undefined,
        teamSnapshot: state.teamConsole.snapshot || undefined,
        reasoning: state.reasoningContent || undefined,
        thinking: state.thinkingContent || undefined,
        tokenUsage: state.tokenUsage,
        modelName: state.modelName,
        error: state.error || undefined,
      };
      setMessages((prev) => [...prev, assistantMessage]);
      reset();
      loadSessions({ silent: true });
    }
  }, [loadSessions, messages, reset, state.clarificationRequests.length, state.currentRunId, state.displaySnapshot, state.error, state.finalAnswerContent, state.hitlRequests.length, state.isStreaming, state.modelName, state.plans.length, state.reasoningContent, state.runCompletedAt, state.runStartedAt, state.skillCalls, state.teamConsole, state.textContent, state.thinkingContent, state.tokenUsage, state.toolCalls, visuallyCompletedRunId]);

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
        className="chat-main-panel chat-workbench-main grid min-h-0 overflow-hidden"
        style={{
          gridTemplateColumns: visibleTeamConsole
            ? 'minmax(0, 1fr) 390px'
            : 'minmax(0, 1fr) 0px',
          transition: 'grid-template-columns 360ms var(--ease-out)',
        }}
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
                  ? state.modelName || runtimeAgent?.modelConfig.primary || 'deepseek-v4-flash'
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
                        sessionId={activeSessionId || ''}
                        onSubmitClarification={handleSubmitClarification}
                      />
                    ))}

                    {hasLiveResponse && (
                      <StreamingBubble
                        isStreaming={state.isStreaming}
                        currentRunId={state.currentRunId}
                        textContent={state.textContent}
                        reasoningContent={state.reasoningContent}
                        finalAnswerContent={state.finalAnswerContent}
                        thinkingContent={state.thinkingContent}
                        displaySnapshot={state.displaySnapshot}
                        teamConsole={state.teamConsole}
                        skillCalls={state.skillCalls}
                        hitlRequests={state.hitlRequests}
                        clarificationRequests={state.clarificationRequests}
                        plans={state.plans}
                        onSubmitClarification={handleSubmitClarification}
                        onResolveHitl={resumeInteraction}
                        onFinalAnswerAnimationChange={handleFinalAnswerAnimationChange}
                        showGeneratedFiles={Boolean(
                          !state.finalAnswerContent ||
                          visuallyCompletedRunId === state.currentRunId
                        )}
                        sessionId={activeSessionId || ''}
                        toolCalls={state.toolCalls}
                        modelName={state.modelName}
                        tokenUsage={state.tokenUsage}
                        error={state.error}
                        runStartedAt={state.runStartedAt}
                        runCompletedAt={state.runCompletedAt}
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
            <FloatingPlanDock plans={state.plans} />
          </div>

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
          />

        </div>

        <div
          className="h-full min-h-0 min-w-0 overflow-hidden"
          style={{
            opacity: visibleTeamConsole ? 1 : 0,
            transform: visibleTeamConsole ? 'translateX(0)' : 'translateX(16px)',
            transition: 'opacity 280ms var(--ease-out), transform 360ms var(--ease-out)',
            pointerEvents: visibleTeamConsole ? 'auto' : 'none',
          }}
          aria-hidden={!visibleTeamConsole}
        >
          {visibleTeamConsole ? (
            <TeamConsolePanel state={visibleTeamConsole} />
          ) : null}
        </div>
      </section>
    </div>
  );
}
