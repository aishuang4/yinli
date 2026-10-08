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
import { Bot, MessageSquare, PanelLeftOpen, Plus, Settings } from 'lucide-react';
import { ScrollToBottomButton } from '../components/ScrollToBottomButton';
import { useMessageScroll } from '../components/useMessageScroll';
import { Tag, Tooltip } from '@/components/design';
import { useEventStream } from '../hooks/useEventStream';
import { useAgents } from '../hooks/useAgents';
import { useOnPageBecameActive } from '../hooks/usePageActive';
import { useAgentsStore } from '../stores/agentsStore';
import { useProjectResources } from '../hooks/useProjectResources';
import { useWorkspaceProjects } from '../hooks/useWorkspaceProjects';
import { useAppStore } from '../stores/appStore';
import { isSurfaceEnabled } from '../stores/productFeatures';
import { useProductFeaturesStore } from '../stores/productFeaturesStore';
import { createId } from '../utils/id';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';
import { MessageBubble, StreamingBubble } from '../features/chat/ChatMessageList';
import { MessageTimeline } from '../features/chat/MessageTimeline';
import { ChatStartScenarios } from '../features/chat/ChatStartScenarios';
import { AskUserComposer } from '../features/chat/AskUserComposer';
import { ChatComposer } from '../features/chat/ChatComposer';
import { MemoryPendingHint } from '../features/chat/MemoryPendingHint';
import { PlanReviewComposer } from '../features/chat/PlanReviewComposer';
import { PlanFloatingPanel } from '../features/chat/PlanFloatingPanel';
import { ChatHistoryPanel } from '../features/chat/ChatHistoryPanel';
import { ChatContextMeter } from '../features/chat/ChatContextMeter';
import { selectLatestContextUsage } from '../features/chat/contextUsage';
import { NotificationBell } from '../features/notifications/NotificationBell';
import { useGroupStudioEntry } from '../features/group-studio/useGroupStudioEntry';
import { selectLatestPlan, selectPendingPlanReview, type PlanReviewData } from '../features/chat/planReviewUtils';
import { selectPendingAskUserInteractions } from '../features/chat/humanInteractionUtils';
import type { ChatFilePayload, ChatSession, LocalMessage, StoredChatMessage } from '../features/chat/types';
import { runtimeError, runtimeText } from '../features/runtime-stream/selectors';

import { useModelCatalog } from '../features/models/useModelCatalog';
import { ModelSelector } from '../features/models/ModelSelector';

import { useChatAttachments } from '../features/chat/useChatAttachments';
import { useEnabledSkills } from '../hooks/useSkills';
import { filterComposerSkills } from '../features/chat/forcedSkills';
import { FilePreviewPane } from '../features/file-preview/FilePreviewPane';
import { isDesktopFilePreviewEnabled, type FilePreviewTarget } from '../features/file-preview/filePreview';

const HISTORY_PANEL_TRANSITION_MS = 260;

export function ChatPage() {
  const { catalog: modelCatalog, loading: modelsLoading, error: modelsError } = useModelCatalog();
  const [modelChoices, setModelChoices] = useState<Record<string, string>>({});
  const [savingModelChoice, setSavingModelChoice] = useState(false);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [targetMode, setTargetMode] = useState<'agent' | 'team'>('agent');
  const [selectedAgentId, setSelectedAgentId] = useState<string>('default');
  const [inputValue, setInputValue] = useState('');
  const [deepThinking, setDeepThinking] = useState(false);
  const [planMode, setPlanMode] = useState(false);
  const [forceCompress, setForceCompress] = useState(false);
  const [forcedSkillIds, setForcedSkillIds] = useState<string[]>([]);
  const [planPanelOpen, setPlanPanelOpen] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [answerAnimating, setAnswerAnimating] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [historyPanelMounted, setHistoryPanelMounted] = useState(true);
  const [previewTarget, setPreviewTarget] = useState<FilePreviewTarget | null>(null);
  const teamModeEnabled = useProductFeaturesStore((state) => isSurfaceEnabled(state.features, 'teamMode'));
  const planModeEnabled = useProductFeaturesStore((state) => isSurfaceEnabled(state.features, 'planMode'));
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
  const { state, sendMessage, resumeInteraction, subscribeRun, abort, steer, reset } = useEventStream();
  const { agents, loading: agentsLoading } = useAgents();
  const { enabledByType, loading: projectResourcesLoading } = useProjectResources(activeProjectId);
  const { skills: enabledSkills, loading: skillsLoading } = useEnabledSkills();
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

  // 打包桌面端 Chat keep-alive 不卸载，回到本页时静默同步 Agent 列表。
  useOnPageBecameActive(() => {
    void useAgentsStore.getState().syncFromServer(activeWorkspaceId, { silent: true });
  });

  useEffect(() => {
    if (!teamModeEnabled && targetMode === 'team') {
      setTargetMode('agent');
    }
  }, [teamModeEnabled, targetMode]);

  useEffect(() => {
    if (!planModeEnabled && planMode) {
      setPlanMode(false);
    }
  }, [planModeEnabled, planMode]);

  const availableAgents = useMemo(() => agents.filter((agent) => (
    agent.role !== 'job'
    && (
      agent.id === 'default'
      || agent.workspaceId === activeWorkspaceId
    )
  )), [activeWorkspaceId, agents]);

  const selectedAgent = useMemo(
    () => availableAgents.find((agent) => agent.id === selectedAgentId)
      || availableAgents.find((agent) => agent.id === 'default')
      || availableAgents[0]
      || null,
    [availableAgents, selectedAgentId]
  );

  const runtimeAgent = selectedAgent;
  const composerSkills = useMemo(
    () => filterComposerSkills(enabledSkills, enabledByType.skill),
    [enabledByType.skill, enabledSkills],
  );

  useEffect(() => {
    const allowed = new Set(composerSkills.map((skill) => skill.id));
    setForcedSkillIds((current) => current.filter((id) => allowed.has(id)));
  }, [composerSkills]);

  const selectedModelId = modelChoices[activeSessionId || 'new'] || sessions.find(s => s.id === activeSessionId)?.modelConfigId || modelCatalog?.defaultModelId || '';
  const selectedModel = modelCatalog?.models.find(m => m.id === selectedModelId);
  const selectedModelSupportsImages = Boolean(selectedModel?.capabilities.images);
  // 顶栏副标题：单行截断，完整内容放 title 供悬停查看。
  const topbarSubtitle = state.isStreaming
    ? selectedModel?.label || '未选择模型'
    : targetMode === 'team'
      ? `${runtimeAgent?.name || 'Assistant'} · Leader 动态组建临时成员`
      : `${runtimeAgent?.name || 'Assistant'} · ${runtimeAgent?.description || 'DeepSeek'}`;
  const selectModel = async (value: string) => {
    const key = activeSessionId || 'new';
    setSavingModelChoice(true);
    try {
      if (activeSessionId && sessions.some(s => s.id === activeSessionId)) {
        const response = await fetch(`/api/chat/sessions/${encodeURIComponent(activeSessionId)}/model?projectId=${encodeURIComponent(activeProjectId)}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ modelConfigId: value }),
        });
        if (!response.ok) { const data = await response.json(); throw new Error(data.detail || '模型选择保存失败'); }
      }
      setModelChoices(current => ({ ...current, [key]: value }));
    } catch (cause) { setChatError(getErrorMessage(cause, '模型选择保存失败')); }
    finally { setSavingModelChoice(false); }
  };
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
  } = useMessageScroll({
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
  const contextUsage = useMemo(
    () => selectLatestContextUsage(snapshot, messages),
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
  const chatRunBlocking = state.isStreaming
    || Boolean(activeAskUserInteraction)
    || Boolean(pendingPlanReview)
    || (Boolean(state.currentRunId) && !snapshot.streamDone);
  const {
    buttonLabel: studioButtonLabel,
    entering: studioEntering,
    flagEnabled: studioFlagEnabled,
    handleToggle: handleStudioToggle,
    shortcutLabel: studioShortcut,
  } = useGroupStudioEntry({
    modelConfigId: selectedModelId,
    activeProjectId,
    activeSessionId,
    agentId: runtimeAgent?.id || 'default',
    chatRunBlocking,
    draftText: inputValue,
    lastUserMessage: [...messages].reverse().find((item) => item.role === 'user' && item.content.trim())?.content ?? '',
    fileAttachments,
    hasImages: imageAttachments.length > 0,
    isComposingRef,
    setActiveSessionId,
    uploadFilesForSession,
  });
  const activeSession = useMemo(() => (
    sessions.find((session) => session.id === activeSessionId) || null
  ), [activeSessionId, sessions]);
  const chatOnlySessions = useMemo(
    () => sessions.filter((session) => !session.groupStudio && !session.group_studio),
    [sessions],
  );
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
    setMessagesLoading(true);
    try {
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
      elapsedSeconds: item.elapsedSeconds,
      clarificationRequests: item.clarificationRequests || item.clarification_requests || [],
      clarificationResolved: item.clarificationResolved || item.clarification_resolved || false,
    }));
    if (messagesRequestRef.current === requestId) {
      setMessages(loadedMessages);
    }
    } catch (error) {
      if (messagesRequestRef.current === requestId) {
        setChatError(getErrorMessage(error, '无法加载历史消息'));
      }
    } finally {
      if (messagesRequestRef.current === requestId) setMessagesLoading(false);
    }
  }, [activeProjectId]);

  const activateSession = useCallback(async (session: ChatSession) => {
    recoveredRunRef.current = '';
    openedPlanIdentityRef.current = '';
    setPlanPanelOpen(false);
    setPreviewTarget(null);
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
    setMessagesLoading(false);
    recoveredRunRef.current = '';
    openedPlanIdentityRef.current = '';
    setPlanPanelOpen(false);
    setPreviewTarget(null);
    setActiveSessionId(null);
    setModelChoices({});
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
    messagesRequestRef.current += 1;
    setMessagesLoading(false);
    pendingSessionRef.current = null;
    recoveredRunRef.current = '';
    openedPlanIdentityRef.current = '';
    setPlanPanelOpen(false);
    setPreviewTarget(null);
    setActiveSessionId(null);
    setModelChoices(previous => {
      const next = { ...previous };
      delete next.new;
      return next;
    });
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

  const filePreviewEnabled = isDesktopFilePreviewEnabled();
  const handleOpenPreview = useCallback((target: FilePreviewTarget) => {
    if (!isDesktopFilePreviewEnabled()) return;
    setPreviewTarget(target);
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
        messagesRequestRef.current += 1;
        setMessagesLoading(false);
        recoveredRunRef.current = '';
        openedPlanIdentityRef.current = '';
        setPlanPanelOpen(false);
        setPreviewTarget(null);
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
    if (!inputValue.trim() && imageAttachments.length === 0 && fileAttachments.length === 0 && !forceCompress) return;
    if (state.isStreaming) {
      const steerText = inputValue.trim();
      if (!steerText || !state.currentRunId) return;
      const userMessage: LocalMessage = {
        id: `user-steer-${Date.now()}`,
        role: 'user',
        content: steerText,
        createdAt: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMessage]);
      setInputValue('');
      setChatError(null);
      try {
        await steer(steerText, { agentId: runtimeAgent?.id, agentName: runtimeAgent?.name });
      } catch (err) {
        setChatError(getErrorMessage(err, '补充消息发送失败。'));
      }
      return;
    }
    if (!activeProjectId) {
      setChatError('请先在设置中选择 Workspace 和 Project。');
      return;
    }
    if (imageAttachments.length > 0 && !selectedModelSupportsImages) {
      setChatError('所选模型未启用图片能力，请切换模型。');
      return;
    }

    const typed = inputValue.trim();
    const userText = typed
      || (fileAttachments.length > 0 ? '请处理我上传的文件。' : imageAttachments.length > 0 ? '请分析这张图片。' : forceCompress ? '请压缩当前会话上下文，然后用一两句话说明是否已整理完毕。' : '');
    if (!userText) return;
    const displayMessage = typed || (forceCompress && fileAttachments.length === 0 && imageAttachments.length === 0 ? '压缩上下文' : undefined);
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
      content: displayMessage || userText,
      createdAt: new Date().toISOString(),
      images,
      files,
    };

    setModelChoices(current => ({ ...current, new: '', [sessionId]: selectedModelId }));
    setActiveSessionId(sessionId);
    resetScroll();
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    resetAttachments();
    setChatError(null);
    const compressThisTurn = forceCompress;
    setForceCompress(false);

    await sendMessage(userText, {
      projectId: activeProjectId,
      sessionId,
      agentId: runtimeAgent?.id,
      agentName: runtimeAgent?.name,
      mode: teamModeEnabled ? targetMode : 'agent',
      modelConfigId: selectedModelId,
      skillIds: runtimeAgent?.skills,
      forcedSkillIds,
      deepThinking,
      planRequested: planModeEnabled && planMode && targetMode === 'agent',
      forceCompress: compressThisTurn,
      displayMessage,
      images,
      files,
    });
  }, [activeProjectId, activeSessionId, deepThinking, fileAttachments, forceCompress, forcedSkillIds, imageAttachments, inputValue, planMode, planModeEnabled, resetAttachments, resetScroll, runtimeAgent, selectedModelId, selectedModelSupportsImages, sendMessage, state.currentRunId, state.isStreaming, steer, targetMode, teamModeEnabled, uploadFilesForSession]);

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
      modelConfigId: selectedModelId,
      skillIds: runtimeAgent?.skills,
      forcedSkillIds,
      deepThinking,
      planId: plan.planId,
      planAction: action,
      planFeedback: feedback,
    });
  }, [activeProjectId, activeSessionId, deepThinking, forcedSkillIds, resetScroll, runtimeAgent, selectedModelId, sendMessage, state.isStreaming]);

  // 当流结束时，将 assistant 回复加入消息历史
  useEffect(() => {
    if (
      !state.isStreaming &&
      !answerAnimating &&
      snapshot.order.length > 0 &&
      (snapshot.streamDone || state.cancelled || Boolean(state.error)) &&
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
        cancelled: state.cancelled,
      };
      setMessages((prev) => [...prev, assistantMessage]);
      reset();
      loadSessions({ silent: true });
    }
  }, [answerAnimating, liveText, loadSessions, messages, projectedError, reset, snapshot, state.cancelled, state.error, state.isStreaming, state.runCompletedAt, state.runStartedAt]);

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
      data-preview-open={Boolean(previewTarget)}
      data-electron-shell={isDesktopShell}
      data-windows-shell={isWindowsDesktopShell}
      style={{
        gridTemplateColumns: chatShellColumns({
          historyMounted: historyPanelMounted,
          historyCollapsed,
          previewOpen: Boolean(previewTarget),
        }),
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
          sessions={chatOnlySessions}
          workspaces={workspaces}
          onCollapse={handleCollapseHistory}
          onDeleteSession={(session) => { void handleDeleteSession(session); }}
          onNewSession={handleNewSession}
          onSelectSession={(session) => { void handleSelectSession(session); }}
        />
      )}

      <section
        className="chat-main-panel chat-workbench-main grid min-h-0 min-w-[280px] grid-cols-1 overflow-hidden"
      >
        <div className="grid min-h-0 min-w-0 grid-cols-[minmax(0,1fr)] grid-rows-[auto_1fr_auto] overflow-hidden">
          <header
            className="chat-workbench-topbar flex min-w-0 items-center justify-between gap-4 overflow-hidden px-8 py-2"
            style={isMacDesktopShell && (!historyPanelMounted || historyCollapsed) ? { paddingLeft: 128 } : undefined}
          >
          <div className="flex min-w-0 flex-1 items-center gap-3 overflow-hidden">
            {historyCollapsed && (
              <div className="flex shrink-0 items-center gap-2">
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
              </div>
            )}
            {studioFlagEnabled ? (
              <Tooltip title={`${studioButtonLabel} ${studioShortcut}`} placement="bottom" mode="dark">
                <button
                  type="button"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)]"
                  onClick={handleStudioToggle}
                  disabled={studioEntering || chatRunBlocking}
                  aria-label={studioButtonLabel}
                >
                  <MessageSquare size={17} strokeWidth={1.7} />
                </button>
              </Tooltip>
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)]">
                <MessageSquare size={17} strokeWidth={1.7} />
              </div>
            )}
            <div className="min-w-0">
              <div className="truncate text-base font-semibold text-[var(--color-text-primary)]">
                {activeSession?.title || '新对话'}
              </div>
              <div className="truncate text-xs text-[var(--color-text-tertiary)]" title={topbarSubtitle}>
                {topbarSubtitle}
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
          <div className="flex shrink-0 items-center gap-3">
            <ChatContextMeter usage={contextUsage} />
            <NotificationBell />
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
              className="h-full min-h-0 overflow-auto px-8 pb-10 pt-4"
              onScroll={handleMessagesScroll}
              onWheel={handleMessagesWheel}
              onPointerDown={cancelProgrammaticScroll}
              role="log"
              aria-live="polite"
              aria-label="对话消息"
            >
              <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-4">
                {messagesLoading && messages.length === 0 && !hasLiveResponse ? (
                  <div role="status" aria-label="正在加载历史消息" className="flex flex-1 flex-col gap-4 py-6">
                    <div className="h-16 w-2/3 self-end rounded-md bg-[var(--color-bg-secondary)]" />
                    <div className="h-40 w-full rounded-md bg-[var(--color-bg-secondary)]" />
                  </div>
                ) : messages.length === 0 && !hasLiveResponse && !chatError ? (
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
                      <div key={msg.id} data-chat-message-id={msg.id}>
                        <MessageBubble
                          message={msg}
                          sessionId={activeSessionId || undefined}
                          previewPath={previewTarget?.path}
                          onOpenPreview={filePreviewEnabled ? handleOpenPreview : undefined}
                        />
                      </div>
                    ))}

                    {hasLiveResponse && (
                      <StreamingBubble
                        snapshot={snapshot}
                        isStreaming={state.isStreaming}
                        cancelled={state.cancelled}
                        modelName={selectedModel?.label || ''}
                        error={state.error}
                        createdAt={state.runCompletedAt || undefined}
                        startedAt={state.runStartedAt || undefined}
                        sessionId={activeSessionId || undefined}
                        previewPath={previewTarget?.path}
                        onOpenPreview={filePreviewEnabled ? handleOpenPreview : undefined}
                        onResolveInteraction={resumeInteraction}
                        onAnswerAnimationChange={setAnswerAnimating}
                      />
                    )}

                    <div ref={messagesEndRef} />
                  </>
                )}
              </div>
            </div>
            <MessageTimeline messages={messages} containerRef={messagesContainerRef} />
            <ScrollToBottomButton
              visible={showScrollToBottom}
              onClick={() => scrollToBottom('smooth')}
            />
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
              isResuming={
                state.resolvingInteractionId === activeAskUserInteraction.hitlRequest.interactionId
              }
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
            <>
            <MemoryPendingHint isStreaming={state.isStreaming} />
            <ChatComposer
            modelSelector={<ModelSelector catalog={modelCatalog} value={selectedModelId}
              onChange={value => void selectModel(value)}
              disabled={savingModelChoice || pendingAskUserInteractions.length > 0}
              loading={modelsLoading} error={modelsError} />}
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
              forceCompress={forceCompress}
              onForceCompressChange={setForceCompress}
              teamModeEnabled={teamModeEnabled}
              planModeEnabled={planModeEnabled}
              composerSkills={composerSkills}
              forcedSkillIds={forcedSkillIds}
              skillsLoading={skillsLoading || projectResourcesLoading}
              onForcedSkillIdsChange={setForcedSkillIds}
            />
            </>
          )}

        </div>
      </section>
      {filePreviewEnabled && previewTarget ? (
        <FilePreviewPane target={previewTarget} onClose={() => setPreviewTarget(null)} />
      ) : null}
    </div>
  );
}

function chatShellColumns({
  historyMounted,
  historyCollapsed,
  previewOpen,
}: {
  historyMounted: boolean;
  historyCollapsed: boolean;
  previewOpen: boolean;
}): string {
  const preview = previewOpen ? ' minmax(280px, min(36vw, 480px))' : '';
  if (!historyMounted) return `minmax(0, 1fr)${preview}`;
  return historyCollapsed
    ? `0px minmax(0, 1fr)${preview}`
    : `286px minmax(0, 1fr)${preview}`;
}
