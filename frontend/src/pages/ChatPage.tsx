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
import { AlertCircle, Bot, Boxes, ChevronDown, FolderKanban, FolderPlus, ImagePlus, MessageSquare, PanelLeftClose, PanelLeftOpen, Plus, Search, Send, Settings, Square, Trash2, X } from 'lucide-react';
import {
  SparkDeepThinkLine,
  SparkReinforcementLearningLine,
  SparkRenyizhuanrenyiAnyToanyLine,
} from '@agentscope-ai/icons';
import { Empty, Select, Spinner, Tag, Tooltip } from '@agentscope-ai/design';
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
import { isAskUserToolName } from '../features/chat/humanInteractionUtils';
import type { ChatImageAttachment, ChatSession, LocalMessage, StoredChatMessage } from '../features/chat/types';
import { DISPLAY_EVENTS, type DisplayItem } from '../types/displayEvents';

const HISTORY_PANEL_TRANSITION_MS = 260;

function pickDisplayItemString(item: DisplayItem, keys: string[]): string {
  for (const key of keys) {
    const value = item.data[key];
    if (value === undefined || value === null) continue;
    const text = String(value);
    if (text) return text;
  }
  return '';
}

function mergeResolvedClarificationFeedbackIntoReplies(messages: LocalMessage[]): LocalMessage[] {
  const merged: LocalMessage[] = [];
  let pendingClarification: DisplayItem | null = null;

  for (const message of messages) {
    if (message.role !== 'assistant') {
      merged.push(message);
      continue;
    }

    const storedAskUserItem = askUserToolItemFromMessage(message);
    if (storedAskUserItem && !message.content.trim()) {
      if (hasToolOutput(storedAskUserItem)) {
        pendingClarification = storedAskUserItem;
        continue;
      }
      merged.push(message);
      continue;
    }

    const clarificationItem = processItemFromResolvedClarificationMessage(message);
    if (clarificationItem && isWaitingClarificationMessage(message)) {
      continue;
    }
    if (clarificationItem && !message.content.trim()) {
      if (hasToolOutput(clarificationItem)) {
        pendingClarification = clarificationItem;
      }
      continue;
    }

    if (pendingClarification) {
      merged.push({
        ...message,
        displaySnapshot: prependProcessItemToMessageSnapshot(message, pendingClarification),
      });
      pendingClarification = null;
      continue;
    }

    merged.push(message);
  }

  if (pendingClarification) {
    merged.push({
      id: `assistant-clarification-${pendingClarification.id}`,
      role: 'assistant',
      content: '',
      createdAt: pendingClarification.createdAt,
      displaySnapshot: snapshotFromProcessItem(pendingClarification, 'waiting'),
    });
  }

  return merged;
}

function askUserToolItemFromMessage(message: LocalMessage): DisplayItem | null {
  const items = (message.displaySnapshot?.actors || [])
    .flatMap((actor) => Array.isArray(actor.items) ? actor.items : []);
  return items.find((item) => (
    item.event.startsWith('tool.') &&
    isAskUserToolName(pickDisplayItemString(item, ['name', 'toolName', 'rawName']))
  )) || null;
}

function hasToolOutput(item: DisplayItem): boolean {
  return Boolean(pickDisplayItemString(item, ['outputPreview', 'output', 'result', 'error']).trim());
}

function isWaitingClarificationMessage(message: LocalMessage): boolean {
  const feedbackBlock = message.displaySnapshot?.blocks?.find((block) => block.block === 'feedback');
  const approval = feedbackBlock?.data?.approval;
  if (!approval || typeof approval !== 'object') return false;
  const status = String((approval as Record<string, unknown>).status || feedbackBlock.status || '');
  return status === 'waiting' && !message.clarificationResolved;
}

function processItemFromResolvedClarificationMessage(message: LocalMessage): DisplayItem | null {
  const feedbackBlock = message.displaySnapshot?.blocks?.find((block) => block.block === 'feedback');
  const approval = feedbackBlock?.data?.approval;
  if (!approval || typeof approval !== 'object') return null;
  const record = approval as Record<string, unknown>;
  if (String(record.kind || record.type || '') !== 'clarification') return null;

  const questions = Array.isArray(record.questions) ? record.questions : [];
  const inputPreview = [
    `模型反问：${String(record.title || '需要补充信息').trim()}`,
    String(record.description || '').trim() ? `说明：${String(record.description || '').trim()}` : '',
    ...questions.flatMap((question, index) => {
      if (!question || typeof question !== 'object') return [];
      const label = String((question as Record<string, unknown>).label || '').trim();
      return label ? [`${index + 1}. ${label}`] : [];
    }),
  ].filter(Boolean).join('\n');
  const outputPreview = extractClarificationAnswerFromMessage(message, record);
  const interactionId = String(record.interactionId || message.id || 'clarification');
  const createdAt = String(record.createdAt || message.createdAt || '');

  return {
    id: `clarification-${interactionId}`,
    event: outputPreview ? DISPLAY_EVENTS.TOOL_COMPLETED : DISPLAY_EVENTS.TOOL_STARTED,
    targetId: `clarification-${interactionId}`,
    status: outputPreview ? 'completed' : 'waiting',
    createdAt,
    updatedAt: message.createdAt || createdAt,
    data: {
      name: 'AskUser',
      inputPreview,
      outputPreview,
      outputSize: outputPreview.length,
      outputTruncated: false,
      error: '',
    },
  };
}

function extractClarificationAnswerFromMessage(message: LocalMessage, approval: Record<string, unknown>): string {
  const fromApproval = extractClarificationAnswerFromApproval(approval);
  if (fromApproval) return fromApproval;
  return extractClarificationAnswerFromMessageContent(message.content);
}

function extractClarificationAnswerFromApproval(approval: Record<string, unknown>): string {
  const preview = approval.externalResultsPreview;
  if (!preview || typeof preview !== 'object' || Array.isArray(preview)) return '';
  const values = Object.values(preview as Record<string, unknown>)
    .map((value) => String(value || '').trim())
    .filter(Boolean);
  return values.join('\n');
}

function extractClarificationAnswerFromMessageContent(content: string): string {
  const text = String(content || '');
  if (!text.includes('针对上一个补充信息请求，用户回答如下')) return '';
  const answerLine = [...text.split('\n')]
    .map((line) => line.trim())
    .reverse()
    .find((line) => line.startsWith('- ') && !line.startsWith('- 原始请求:') && line.includes(':'));
  if (!answerLine) return '';
  const [, answer = ''] = answerLine.split(/:(.*)/s);
  return answer.trim();
}

function prependProcessItemToMessageSnapshot(message: LocalMessage, item: DisplayItem) {
  const snapshot = message.displaySnapshot;
  const resolvedItem = resolveClarificationProcessItem(item);
  if (!snapshot) return snapshotFromProcessItem(resolvedItem, message.error ? 'failed' : 'completed');
  const actors = Array.isArray(snapshot.actors) ? [...snapshot.actors] : [];
  if (actors.length === 0) {
    return {
      ...snapshot,
      actors: snapshotFromProcessItem(resolvedItem, message.error ? 'failed' : 'completed').actors,
    };
  }
  const firstActor = actors[0];
  const existingItems = Array.isArray(firstActor.items) ? firstActor.items : [];
  if (existingItems.some((existing) => existing.id === resolvedItem.id || existing.targetId === resolvedItem.targetId)) {
    return snapshot;
  }
  actors[0] = {
    ...firstActor,
    items: [resolvedItem, ...existingItems],
  };
  return { ...snapshot, actors };
}

function resolveClarificationProcessItem(item: DisplayItem): DisplayItem {
  const inputPreview = pickDisplayItemString(item, ['inputPreview', 'input', 'args']);
  const storedOutput = pickDisplayItemString(item, ['outputPreview', 'output', 'result', 'error']);
  return {
    ...item,
    event: storedOutput ? DISPLAY_EVENTS.TOOL_COMPLETED : item.event,
    status: storedOutput ? 'completed' : item.status,
    data: {
      ...item.data,
      inputPreview,
      input: inputPreview,
      outputPreview: storedOutput,
      output: storedOutput,
      outputSize: storedOutput.length,
      outputTruncated: false,
      error: '',
    },
  };
}

function snapshotFromProcessItem(item: DisplayItem, status: string) {
  return {
    version: 3 as const,
    mode: 'single_agent' as const,
    actors: [{
      id: 'single-agent',
      role: 'single' as const,
      agentId: '',
      agentName: 'Agent',
      status,
      task: '',
      detail: '',
      output: '',
      createdAt: item.createdAt || '',
      updatedAt: item.updatedAt,
      completedAt: '',
      items: [item],
      data: {},
    }],
    blocks: [],
  };
}


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
  const { workspaces, projects, allProjects, selectWorkspace, selectProject } = useWorkspaceProjects();
  const setActiveContext = useAppStore((state) => state.setActiveContext);

  const availableAgents = useMemo(() => agents.filter((agent) => (
    agent.workspaceId === activeWorkspaceId && enabledByType.agent.has(agent.id)
  )), [activeWorkspaceId, agents, enabledByType]);

  const selectedAgent = useMemo(
    () => availableAgents.find((agent) => agent.id === selectedAgentId) || null,
    [availableAgents, selectedAgentId]
  );

  const runtimeAgent = selectedAgent;

  const agentOptions = useMemo(() => [
    { label: 'Assistant - 默认助手', value: 'default' },
    ...availableAgents.map((agent) => ({
      label: agent.name,
      value: agent.id,
    })),
  ], [availableAgents]);

  const selectedModelId = runtimeAgent?.modelConfig.primary || 'deepseek-v4-flash';
  const selectedModelSupportsImages = selectedModelId === 'qwen3.7-plus';
  const isDesktopShell = Boolean(window.electronAPI);
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
  const historyGroups = useMemo(() => workspaces.map((workspace) => ({
    workspace,
    projects: allProjects
      .filter((project) => project.workspaceId === workspace.id)
      .map((project) => ({
        project,
        sessions: sessions.filter((session) => session.project_id === project.id),
      }))
      .filter((group) => group.sessions.length > 0),
  })).filter((group) => group.projects.length > 0), [allProjects, sessions, workspaces]);
  const activeWorkspace = workspaces.find((workspace) => workspace.id === activeWorkspaceId) || null;
  const activeProject = allProjects.find((project) => project.id === activeProjectId) || null;
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
    setChatError(null);
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    reset();
  }, [reset, state.isStreaming]);

  const handleCreateProjectFromDirectory = useCallback(async () => {
    if (state.isStreaming) return;
    if (!window.electronAPI) {
      setChatError('请在 Electron 桌面端选择 Project 文件夹。');
      return;
    }
    setChatError(null);
    try {
      const selected = await window.electronAPI.selectProjectDirectory();
      if (selected.canceled || !selected.path || !selected.name) return;
      const existing = allProjects.find((project) => (
        project.workspaceId === activeWorkspaceId && project.rootPath === selected.path
      ));
      if (existing) {
        handleNewSession();
        setActiveContext(existing.workspaceId, existing.id);
        notifySuccess(`已切换到 Project：${existing.name}`);
        return;
      }
      const response = await fetch(`/api/workspaces/${encodeURIComponent(activeWorkspaceId)}/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: selected.name,
          description: '',
          rootPath: selected.path,
          instructionsFile: '',
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(String(payload.detail || `HTTP ${response.status}`));
      }
      const project = await response.json() as { id: string; workspaceId: string; name: string };
      handleNewSession();
      setActiveContext(project.workspaceId, project.id);
      notifySuccess(`Project ${project.name} 已创建`);
    } catch (err) {
      setChatError(err instanceof Error ? err.message : 'Project 创建失败。');
    }
  }, [activeWorkspaceId, allProjects, handleNewSession, setActiveContext, state.isStreaming]);

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

  /** 发送消息 */
  const handleSend = useCallback(async () => {
    if ((!inputValue.trim() && imageAttachments.length === 0) || state.isStreaming) return;
    if (!activeProjectId) {
      setChatError('请先为新对话选择或创建 Project。');
      return;
    }
    if (imageAttachments.length > 0 && !selectedModelSupportsImages) {
      setChatError(`${selectedModelId} 不支持图片输入，请选择使用 qwen3.7-plus 的 Agent。`);
      return;
    }

    const userText = inputValue.trim() || '请分析这张图片。';
    const sessionId = activeSessionId || createId();
    const images = imageAttachments.map(({ name, media_type, data }) => ({
      name,
      media_type,
      data,
    }));
    const userMessage: LocalMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: userText,
      createdAt: new Date().toISOString(),
      images,
    };

    setActiveSessionId(sessionId);
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setImageAttachments([]);
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
    });
  }, [activeProjectId, activeSessionId, deepThinking, imageAttachments, inputValue, runtimeAgent, selectedModelId, selectedModelSupportsImages, sendMessage, state.isStreaming, targetMode]);

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
        <aside
          className="chat-history-panel chat-workbench-history flex min-h-0 flex-col overflow-hidden"
          data-collapsed={historyCollapsed}
        >
          {isDesktopShell && <div className="chat-workbench-electron-spacer" aria-hidden="true" />}
          <div className="px-4 pb-3 pt-3">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
                历史对话
              </h2>
              <div className="flex items-center gap-2">
                <Tooltip title="折叠历史对话" placement="bottom" mode="dark">
                  <button
                    type="button"
                    className="icon-button chat-history-collapse-button"
                    onClick={handleCollapseHistory}
                    aria-label="折叠历史对话"
                  >
                    <PanelLeftClose size={17} strokeWidth={1.7} />
                  </button>
                </Tooltip>
                <button
                  type="button"
                  className="icon-button chat-history-new-button"
                  onClick={handleNewSession}
                  disabled={state.isStreaming}
                  aria-label="新建会话"
                  title="新建会话"
                >
                  <Plus size={15} />
                </button>
              </div>
            </div>
            <div className="chat-workbench-search flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-[var(--color-text-tertiary)]">
              <Search size={14} />
              <span>{sessions.length} 个会话</span>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
            {sessionsLoading || (sessions.length > 0 && allProjects.length === 0) ? (
              <div className="flex justify-center py-8">
                <Spinner />
              </div>
            ) : sessions.length === 0 ? (
              <Empty type="noData" description="暂无历史对话" />
            ) : (
              <div className="space-y-5">
                {historyGroups.map(({ workspace, projects: projectGroups }) => (
                  <section key={workspace.id} aria-label={`${workspace.name}中的历史对话`}>
                    <div className="mb-2 flex items-center gap-2 px-2 text-xs font-semibold text-[var(--color-text-secondary)]">
                      <Boxes size={13} strokeWidth={1.8} />
                      <span className="truncate">{workspace.name}</span>
                    </div>
                    <div className="space-y-3">
                      {projectGroups.map(({ project, sessions: projectSessions }) => (
                        <div key={project.id}>
                          <div className="mb-1 flex items-center gap-2 px-2 text-xs text-[var(--color-text-tertiary)]">
                            <FolderKanban size={12} strokeWidth={1.7} />
                            <span className="truncate">{project.name}</span>
                            <span className="ml-auto shrink-0">{projectSessions.length}</span>
                          </div>
                          <div className="space-y-1">
                            {projectSessions.map((session) => (
                              <button
                                key={session.id}
                                type="button"
                                onClick={() => handleSelectSession(session)}
                                className={`chat-session-item group w-full rounded-lg px-3 py-2 text-left transition-all duration-300 ease-out ${
                                  activeSessionId === session.id
                                    ? 'chat-session-item--active'
                                    : 'hover:bg-[var(--color-bg-hover)]'
                                }`}
                              >
                                <div className="flex items-start gap-2">
                                  <div className="min-w-0 flex-1">
                                    <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                                      {session.title}
                                    </div>
                                    <div className="mt-1 flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
                                      <span className="truncate">{session.agent_name}</span>
                                      <span>{session.message_count} 条</span>
                                    </div>
                                  </div>
                                  <span
                                    role="button"
                                    tabIndex={0}
                                    className="rounded p-1 text-[var(--color-text-tertiary)] opacity-0 transition-all duration-300 ease-out hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-error)] group-hover:opacity-100"
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      void handleDeleteSession(session);
                                    }}
                                    onKeyDown={(event) => {
                                      if (event.key === 'Enter' || event.key === ' ') {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        void handleDeleteSession(session);
                                      }
                                    }}
                                    aria-label="删除会话"
                                  >
                                    <Trash2 size={14} />
                                  </span>
                                </div>
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </div>
        </aside>
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
            style={isDesktopShell && (!historyPanelMounted || historyCollapsed) ? { paddingLeft: 128 } : undefined}
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
                      先选择一个主 Agent，再输入消息开始协作。系统会保存会话历史，方便后续继续查看。
                    </div>
                    <div className="mt-3 w-full max-w-[520px] border-t border-[var(--color-border)] pt-4 text-left">
                      <div className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">保存到</div>
                      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_36px] gap-2">
                        <Select
                          aria-label="为新对话选择 Workspace"
                          value={activeWorkspace?.id || undefined}
                          options={workspaces.map((workspace) => ({ label: workspace.name, value: workspace.id }))}
                          onChange={(value) => void selectWorkspace(String(value))}
                          placeholder="Workspace"
                          style={{ minWidth: 0, width: '100%' }}
                        />
                        <Select
                          aria-label="为新对话选择 Project"
                          value={activeProject?.id || undefined}
                          options={projects.map((project) => ({ label: project.name, value: project.id }))}
                          onChange={(value) => selectProject(String(value))}
                          placeholder="Project"
                          disabled={projects.length === 0}
                          style={{ minWidth: 0, width: '100%' }}
                        />
                        <Tooltip title="选择文件夹创建 Project" placement="bottom" mode="dark">
                          <button
                            type="button"
                            className="icon-button h-8 w-8"
                            aria-label="选择文件夹创建 Project"
                            onClick={() => void handleCreateProjectFromDirectory()}
                          >
                            <FolderPlus size={15} />
                          </button>
                        </Tooltip>
                      </div>
                    </div>
                  </div>
                ) : (
                  <>
                    {messages.map((msg) => (
                      <MessageBubble
                        key={msg.id}
                        message={msg}
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

          <div className="px-8 pb-6 pt-2">
          {chatError && (
            <div className="mb-2 flex items-center gap-2 rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] px-3 py-2 text-sm text-[var(--color-error)]">
              <AlertCircle size={14} />
              {chatError}
            </div>
          )}

          <div className="chat-composer mx-auto max-w-4xl rounded-xl bg-[var(--color-bg-secondary)]">
            <input
              ref={imageInputRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(event) => handleImageFiles(event.target.files)}
            />
            <textarea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              onCompositionStart={() => {
                isComposingRef.current = true;
              }}
              onCompositionEnd={() => {
                isComposingRef.current = false;
              }}
              placeholder={state.isStreaming ? 'Agent 正在回复中...' : '输入消息...'}
              rows={2}
              disabled={state.isStreaming}
              className="block max-h-[180px] w-full resize-none rounded-t-lg border-0 bg-transparent px-4 pb-0 pt-3 text-base text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:bg-[var(--color-bg-tertiary)]"
              aria-label="消息输入框"
            />

            {imageAttachments.length > 0 && (
              <div className="flex max-h-[88px] flex-wrap gap-2 overflow-hidden px-3 py-2">
                {imageAttachments.map((image) => (
                  <div
                    key={image.id}
                    className="group relative h-16 w-16 overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)]"
                  >
                    <img
                      src={image.previewUrl}
                      alt={image.name}
                      className="h-full w-full object-cover"
                    />
                    <button
                      type="button"
                      onClick={() => removeImageAttachment(image.id)}
                      className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded bg-black/60 text-white opacity-0 transition-opacity group-hover:opacity-100"
                      aria-label="移除图片"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2 px-3 py-2">
              <Tooltip title="上传图片" placement="top" mode="dark">
                <span className="inline-flex">
                  <button
                    type="button"
                    className="icon-button h-8 w-8 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] text-[var(--color-text-secondary)] transition-colors duration-200 hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
                    onClick={() => imageInputRef.current?.click()}
                    disabled={state.isStreaming || imageAttachments.length >= 4}
                    aria-label="上传图片"
                  >
                    <ImagePlus size={16} />
                  </button>
                </span>
              </Tooltip>
              <div className="flex rounded-md bg-[var(--color-bg-primary)] p-1">
                <Tooltip title="单 Agent" placement="top" mode="dark">
                  <span className="inline-flex">
                    <button
                      type="button"
                      className={`icon-button h-8 w-8 rounded-sm border transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-40 ${
                        targetMode === 'agent'
                          ? 'border-[var(--color-text-primary)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] shadow-sm'
                          : 'border-transparent bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
                      }`}
                      disabled={state.isStreaming}
                      onClick={() => setTargetMode('agent')}
                      aria-label="单 Agent"
                      aria-pressed={targetMode === 'agent'}
                    >
                      <SparkReinforcementLearningLine size={16} />
                    </button>
                  </span>
                </Tooltip>
                <Tooltip title="Team" placement="top" mode="dark">
                  <span className="inline-flex">
                    <button
                      type="button"
                      className={`icon-button h-8 w-8 rounded-sm border transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-40 ${
                        targetMode === 'team'
                          ? 'border-[var(--color-text-primary)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] shadow-sm'
                          : 'border-transparent bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
                      }`}
                      disabled={state.isStreaming}
                      onClick={() => setTargetMode('team')}
                      aria-label="Team"
                      aria-pressed={targetMode === 'team'}
                    >
                      <SparkRenyizhuanrenyiAnyToanyLine size={16} />
                    </button>
                  </span>
                </Tooltip>
              </div>
              {targetMode === 'agent' && (
                <Select
                  value={selectedAgentId}
                  onChange={(value) => setSelectedAgentId(value)}
                  options={agentOptions}
                  loading={agentsLoading || projectResourcesLoading}
                  disabled={state.isStreaming}
                  placeholder="选择 Agent"
                  style={{ width: 160 }}
                />
              )}
              <div className="ml-auto flex items-center gap-2">
                <Tooltip title={deepThinking ? '深度思考已开启' : '开启深度思考'} placement="top" mode="dark">
                  <span className="inline-flex">
                    <button
                      type="button"
                      className={`icon-button h-8 w-8 rounded-md border transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-40 ${
                        deepThinking
                          ? 'border-[var(--color-text-primary)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] shadow-sm'
                          : 'border-[var(--color-border)] bg-[var(--color-bg-primary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
                      }`}
                      onClick={() => setDeepThinking((value) => !value)}
                      disabled={state.isStreaming}
                      aria-label={deepThinking ? '关闭深度思考' : '开启深度思考'}
                      aria-pressed={deepThinking}
                    >
                      <SparkDeepThinkLine size={16} />
                    </button>
                  </span>
                </Tooltip>
                {state.isStreaming ? (
                  <Tooltip title="停止回复" placement="top" mode="dark">
                    <span className="inline-flex">
                      <button
                        type="button"
                        className="icon-button h-8 w-8 rounded-md border border-[var(--color-text-primary)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] transition-opacity duration-200 hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                        onClick={abort}
                        aria-label="停止回复"
                      >
                        <Square size={16} />
                      </button>
                    </span>
                  </Tooltip>
                ) : (
                  <Tooltip title="发送消息" placement="top" mode="dark">
                    <span className="inline-flex">
                      <button
                        type="button"
                        className="icon-button h-8 w-8 rounded-md border border-[var(--color-text-primary)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] transition-opacity duration-200 hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                        onClick={handleSend}
                        disabled={!inputValue.trim() && imageAttachments.length === 0}
                        aria-label="发送消息"
                      >
                        <Send size={16} />
                      </button>
                    </span>
                  </Tooltip>
                )}
              </div>
            </div>
          </div>
          </div>
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
