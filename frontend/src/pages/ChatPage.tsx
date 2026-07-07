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
import { AlertCircle, Bot, ChevronDown, ImagePlus, Plus, Send, Square, Trash2, X } from 'lucide-react';
import {
  SparkDeepThinkLine,
  SparkReinforcementLearningLine,
  SparkRenyizhuanrenyiAnyToanyLine,
} from '@agentscope-ai/icons';
import { Empty, Select, Spinner, Tag, Tooltip } from '@agentscope-ai/design';
import { useEventStream, type TeamActorInfo, type ToolCallInfo } from '../hooks/useEventStream';
import { useAgents } from '../hooks/useAgents';
import { useTeams } from '../hooks/useTeams';
import { createId } from '../utils/id';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';
import { MessageBubble, StreamingBubble } from '../features/chat/ChatMessageList';
import { TeamActorSidebar } from '../features/chat/TeamDisplayBlock';
import { TeamSelectionDock } from '../features/chat/TeamSelectionDock';
import { FloatingPlanDock } from '../features/chat/FloatingPlanDock';
import { isAskUserToolName } from '../features/chat/humanInteractionUtils';
import type { ChatImageAttachment, ChatImagePayload, ChatSession, LocalMessage, PendingTeamSelection, StoredChatMessage } from '../features/chat/types';
import { DISPLAY_EVENTS, type DisplayActor, type DisplayItem } from '../types/displayEvents';


function teamActorsFromMessageSnapshot(message: LocalMessage): TeamActorInfo[] {
  const snapshot = message.displaySnapshot;
  if (!snapshot || snapshot.mode !== 'team') return [];
  return snapshot.actors.map(teamActorFromDisplayActor);
}

function teamActorFromDisplayActor(actor: DisplayActor): TeamActorInfo {
  return {
    id: actor.activityId || actor.id,
    agentId: actor.agentId,
    agentName: actor.agentName,
    role: actor.role,
    status: normalizeTeamActorStatus(actor.status),
    task: actor.task,
    assignment: actor.assignment,
    expectedOutput: actor.expectedOutput,
    constraints: actor.constraints,
    detail: actor.detail,
    output: actor.output,
    report: actor.report,
    skippedReason: actor.skippedReason,
    reportReviewStatus: actor.reportReviewStatus,
    reportReviewNotes: actor.reportReviewNotes,
    reportReviewGaps: actor.reportReviewGaps,
    thinking: actor.items
      .filter((item) => item.event === DISPLAY_EVENTS.THINKING_DELTA)
      .map((item) => pickDisplayItemString(item, ['text', 'content', 'delta']))
      .join('\n\n'),
    currentEvent: actor.detail,
    items: actor.items,
    toolCalls: actor.items
      .filter((item) => item.event.startsWith('tool.') || item.targetId.startsWith('tool'))
      .map(toolCallFromDisplayItem),
    executionMode: actor.executionMode,
    dependsOn: actor.dependsOn,
    waitReason: actor.waitReason,
    dependencyOutputCount: actor.dependencyOutputCount,
    leaderContextCount: actor.leaderContextCount,
    createdAt: actor.createdAt,
  };
}

function normalizeTeamActorStatus(status: string): TeamActorInfo['status'] {
  if (
    status === 'running' ||
    status === 'waiting' ||
    status === 'completed' ||
    status === 'failed' ||
    status === 'skipped'
  ) {
    return status;
  }
  return 'pending';
}

function toolCallFromDisplayItem(item: DisplayItem): ToolCallInfo {
  return {
    id: item.targetId || item.id,
    name: pickDisplayItemString(item, ['name', 'toolName', 'rawName']) || 'tool',
    args: pickDisplayItemString(item, ['inputPreview', 'input', 'args']),
    result: pickDisplayItemString(item, ['outputPreview', 'output', 'result', 'error']),
    status: normalizeToolCallStatus(String(item.status || item.data.status || 'running')),
  };
}

function normalizeToolCallStatus(status: string): ToolCallInfo['status'] {
  if (status === 'running' || status === 'completed' || status === 'failed' || status === 'calling') {
    return status;
  }
  return 'running';
}

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
    mode: 'single_agent',
    actors: [{
      id: 'single-agent',
      role: 'single',
      agentId: '',
      agentName: 'Agent',
      activityId: null,
      status,
      task: '',
      assignment: '',
      expectedOutput: '',
      constraints: '',
      detail: '',
      output: '',
      report: '',
      skippedReason: '',
      reportReviewStatus: '',
      reportReviewNotes: '',
      reportReviewGaps: [],
      executionMode: '',
      dependsOn: [],
      waitReason: '',
      dependencyOutputCount: 0,
      leaderContextCount: 0,
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
  const [selectedTeamId, setSelectedTeamId] = useState<string>('');
  const [selectingTeamWorkers, setSelectingTeamWorkers] = useState(false);
  const [pendingTeamSelection, setPendingTeamSelection] = useState<PendingTeamSelection | null>(null);
  const [inputValue, setInputValue] = useState('');
  const [deepThinking, setDeepThinking] = useState(false);
  const [imageAttachments, setImageAttachments] = useState<ChatImageAttachment[]>([]);
  const [chatError, setChatError] = useState<string | null>(null);
  const [selectedTeamActorId, setSelectedTeamActorId] = useState<string | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const programmaticScrollRef = useRef(false);
  const programmaticScrollTimerRef = useRef<number | null>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);
  const recoveredRunRef = useRef<string>('');
  const completedVisualRunRef = useRef<string>('');
  const [visualCompletionVersion, setVisualCompletionVersion] = useState(0);
  const { state, sendMessage, resumeInteraction, subscribeRun, abort, reset } = useEventStream();
  const { agents, loading: agentsLoading } = useAgents();
  const { teams, loading: teamsLoading } = useTeams();

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) || null,
    [agents, selectedAgentId]
  );

  const selectedTeam = useMemo(
    () => teams.find((team) => team.id === selectedTeamId) || null,
    [selectedTeamId, teams]
  );

  const teamLeaderAgent = useMemo(() => (
    selectedTeam
      ? agents.find((agent) => agent.id === selectedTeam.leaderAgentId) || null
      : null
  ), [agents, selectedTeam]);

  const runtimeAgent = targetMode === 'team' ? teamLeaderAgent : selectedAgent;

  const agentOptions = useMemo(() => [
    { label: 'Assistant - 默认助手', value: 'default' },
    ...agents.map((agent) => ({
      label: agent.name,
      value: agent.id,
    })),
  ], [agents]);

  const teamOptions = useMemo(() => (
    teams.map((team) => ({
      label: team.name,
      value: team.id,
    }))
  ), [teams]);

  const selectedModelId = runtimeAgent?.modelConfig.primary || 'deepseek-chat';
  const selectedModelSupportsImages = selectedModelId === 'qwen3.7-plus';
  const hasLiveResponse = (
    state.isStreaming ||
    state.hitlRequests.length > 0 ||
    state.clarificationRequests.length > 0 ||
    Boolean(state.finalAnswerContent) ||
    Boolean(state.reasoningContent) ||
    Boolean(state.thinkingContent) ||
    state.toolCalls.length > 0 ||
    state.skillCalls.length > 0 ||
    state.teamActors.length > 0 ||
    state.displaySnapshot.blocks.length > 0 ||
    state.plans.length > 0
  );

  const visibleTeamActors = useMemo(() => (
    [
      ...messages.flatMap((message) => teamActorsFromMessageSnapshot(message)),
      ...state.teamActors,
    ]
  ), [messages, state.teamActors]);

  const selectedTeamActor = useMemo(() => (
    visibleTeamActors.find((activity) => activity.id === selectedTeamActorId) || null
  ), [selectedTeamActorId, visibleTeamActors]);
  const openTeamActorSidebar = useCallback((actorId: string) => {
    setSelectedTeamActorId(actorId);
  }, []);

  const handleFinalAnswerAnimationChange = useCallback((isAnimating: boolean) => {
    const runId = state.currentRunId;
    if (!runId) return;
    if (isAnimating) {
      if (completedVisualRunRef.current === runId) {
        completedVisualRunRef.current = '';
        setVisualCompletionVersion((version) => version + 1);
      }
      return;
    }
    if (completedVisualRunRef.current !== runId) {
      completedVisualRunRef.current = runId;
      setVisualCompletionVersion((version) => version + 1);
    }
  }, [state.currentRunId]);

  const buildTeamMembersForRun = useCallback((workerAgentIds: string[] | null) => {
    if (!selectedTeam) return [];
    if (workerAgentIds === null) return selectedTeam.members;
    const selectedWorkers = new Set(workerAgentIds);
    return selectedTeam.members.filter((member) => (
      member.role === 'leader' || selectedWorkers.has(member.agentId)
    ));
  }, [selectedTeam]);

  const loadSessions = useCallback(async (options?: { silent?: boolean }) => {
    const silent = Boolean(options?.silent);
    try {
      if (!silent) {
        setSessionsLoading(true);
      }
      const response = await fetch('/api/chat/sessions');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data: ChatSession[] = await response.json();
      setSessions(data);
    } finally {
      if (!silent) {
        setSessionsLoading(false);
      }
    }
  }, []);

  const loadSessionMessages = useCallback(async (sessionId: string) => {
    const response = await fetch(`/api/chat/sessions/${sessionId}/messages`);
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
      clarificationRequests: item.clarificationRequests || item.clarification_requests || [],
      clarificationResolved: item.clarificationResolved || item.clarification_resolved || false,
    }));
    setMessages(mergeResolvedClarificationFeedbackIntoReplies(loadedMessages));
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    if (!activeSessionId || hasLiveResponse) return;
    let cancelled = false;

    async function recoverActiveRun() {
      try {
        const response = await fetch('/api/chat/runs');
        if (!response.ok) return;
        const data = await response.json();
        const runs = Array.isArray(data.runs) ? data.runs : [];
        const activeRun = runs.find((run: Record<string, unknown>) => {
          const sessionId = String(run.sessionId || run.session_id || '');
          const status = String(run.status || '');
          return (
            sessionId === activeSessionId &&
            (status === 'running' || status === 'waiting' || status === 'cancelling')
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
  }, [activeSessionId, hasLiveResponse, subscribeRun]);

  useEffect(() => {
    if (targetMode === 'team' && !selectedTeamId && teams.length > 0) {
      setSelectedTeamId(teams[0].id);
    }
  }, [selectedTeamId, targetMode, teams]);

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
  }, [messages, scrollToBottom, state.isStreaming, state.finalAnswerContent, state.reasoningContent, state.thinkingContent, state.toolCalls, state.skillCalls, state.teamActors]);

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
    recoveredRunRef.current = '';
    setActiveSessionId(null);
    setMessages([]);
    setImageAttachments([]);
    setChatError(null);
    setSelectedTeamActorId(null);
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    reset();
  }, [reset, state.isStreaming]);

  const handleSelectSession = useCallback(async (session: ChatSession) => {
    if (state.isStreaming) return;
    recoveredRunRef.current = '';
    reset();
    setActiveSessionId(session.id);
    if (session.team_id) {
      setTargetMode('team');
      setSelectedTeamId(session.team_id);
    } else {
      setTargetMode('agent');
      setSelectedAgentId(session.agent_id || 'default');
    }
    setImageAttachments([]);
    setChatError(null);
    setSelectedTeamActorId(null);
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    reset();
    await loadSessionMessages(session.id);
  }, [loadSessionMessages, reset, state.isStreaming]);

  const handleDeleteSession = useCallback(async (sessionId: string) => {
    if (state.isStreaming) return;
    try {
      const response = await fetch(`/api/chat/sessions/${sessionId}`, { method: 'DELETE' });
      if (!response.ok && response.status !== 404) throw new Error(`HTTP ${response.status}`);
      setSessions((current) => current.filter((session) => session.id !== sessionId));
      if (activeSessionId === sessionId) {
        recoveredRunRef.current = '';
        setActiveSessionId(null);
        setMessages([]);
        setImageAttachments([]);
        setChatError(null);
        setSelectedTeamActorId(null);
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

  const prepareTeamSelection = useCallback(async (message: string, images: ChatImagePayload[]) => {
    if (!selectedTeam || targetMode !== 'team') return;
    setSelectingTeamWorkers(true);
    setPendingTeamSelection(null);
    try {
      const response = await fetch('/api/chat/team-worker-selection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          agentId: runtimeAgent?.id,
          agentName: runtimeAgent?.name,
          teamName: selectedTeam.name,
          teamMembers: selectedTeam.members,
          workerSelectorPrompt: selectedTeam.workerSelectorPrompt,
          model: runtimeAgent?.modelConfig.primary,
        }),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }
      const result = await response.json() as { selectedAgentIds?: string[]; reason?: string };
      setPendingTeamSelection({
        message,
        images,
        selectedAgentIds: result.selectedAgentIds || [],
        reason: result.reason || '模型已完成 Worker 预选。',
      });
    } catch (err) {
      setPendingTeamSelection({
        message,
        images,
        selectedAgentIds: selectedTeam.members
          .filter((member) => member.role === 'worker')
          .map((member) => member.agentId),
        reason: err instanceof Error ? `AI 选择失败，默认使用全部 Worker：${err.message}` : 'AI 选择失败，默认使用全部 Worker。',
      });
    } finally {
      setSelectingTeamWorkers(false);
    }
  }, [runtimeAgent, selectedTeam, targetMode]);

  /** 发送消息 */
  const handleSend = useCallback(async () => {
    if ((!inputValue.trim() && imageAttachments.length === 0) || state.isStreaming) return;
    if (targetMode === 'team' && !selectedTeam) {
      setChatError('请先选择一个 Team。');
      return;
    }
    if (targetMode === 'team' && !teamLeaderAgent) {
      setChatError('当前 Team 没有可用的 Leader Agent。');
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
    if (targetMode === 'team') {
      await prepareTeamSelection(userText, images);
      return;
    }

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
      sessionId,
      agentId: runtimeAgent?.id,
      agentName: runtimeAgent?.name,
      systemPrompt: runtimeAgent?.systemPrompt,
      model: runtimeAgent?.modelConfig.primary,
      skillIds: runtimeAgent?.skills,
      deepThinking,
      images,
    });
  }, [activeSessionId, deepThinking, imageAttachments, inputValue, prepareTeamSelection, runtimeAgent, selectedModelId, selectedModelSupportsImages, selectedTeam, sendMessage, state.isStreaming, targetMode, teamLeaderAgent]);

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

  const confirmTeamSelection = useCallback(async (workerAgentIds: string[]) => {
    if (!pendingTeamSelection || state.isStreaming) return;
    const sessionId = activeSessionId || createId();
    const userMessage: LocalMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: pendingTeamSelection.message,
      createdAt: new Date().toISOString(),
      images: pendingTeamSelection.images,
    };

    setActiveSessionId(sessionId);
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setImageAttachments([]);
    setChatError(null);
    setPendingTeamSelection(null);

    await sendMessage(pendingTeamSelection.message, {
      sessionId,
      agentId: runtimeAgent?.id,
      agentName: runtimeAgent?.name,
      teamId: selectedTeam?.id,
      teamName: selectedTeam?.name,
      workerSelectorPrompt: selectedTeam?.workerSelectorPrompt,
      teamMembers: buildTeamMembersForRun(workerAgentIds).map((member) => ({
        id: member.id,
        agentId: member.agentId,
        name: agents.find((agent) => agent.id === member.agentId)?.name || member.name,
        role: member.role,
        description: member.description,
        executionMode: member.executionMode,
        dependsOn: member.dependsOn,
        handoffPrompt: member.handoffPrompt,
      })),
      systemPrompt: runtimeAgent?.systemPrompt,
      model: runtimeAgent?.modelConfig.primary,
      skillIds: runtimeAgent?.skills,
      deepThinking,
      images: pendingTeamSelection.images,
    });
  }, [activeSessionId, agents, buildTeamMembersForRun, deepThinking, pendingTeamSelection, runtimeAgent, selectedTeam, sendMessage, state.isStreaming]);

  // 当流结束时，将 assistant 回复加入消息历史
  useEffect(() => {
    const waitingForFinalAnswerAnimation = Boolean(
      state.currentRunId &&
      state.finalAnswerContent &&
      completedVisualRunRef.current !== state.currentRunId,
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
  }, [loadSessions, messages, reset, state.clarificationRequests.length, state.currentRunId, state.displaySnapshot, state.error, state.finalAnswerContent, state.hitlRequests.length, state.isStreaming, state.modelName, state.plans.length, state.reasoningContent, state.runCompletedAt, state.runStartedAt, state.skillCalls, state.teamActors, state.textContent, state.thinkingContent, state.tokenUsage, state.toolCalls, visualCompletionVersion]);

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
    <div className="chat-page-shell grid h-full min-h-0 grid-cols-[280px_1fr] gap-4 bg-[var(--color-bg-secondary)] p-4">
      <aside className="chat-history-panel flex min-h-0 flex-col overflow-hidden rounded-xl bg-[var(--color-bg-primary)]">
        <div className="p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-medium text-[var(--color-text-primary)]">
              历史对话
            </h2>
            <button
              type="button"
              className="icon-button h-8 w-8 rounded-md bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] transition-all duration-300 ease-out hover:-translate-y-0.5 hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              onClick={handleNewSession}
              disabled={state.isStreaming}
              aria-label="新建会话"
              title="新建会话"
            >
              <Plus size={14} />
            </button>
          </div>
          <div className="text-xs text-[var(--color-text-tertiary)]">
            {sessions.length} 个会话
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {sessionsLoading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : sessions.length === 0 ? (
            <Empty type="noData" description="暂无历史对话" />
          ) : (
            <div className="space-y-2">
              {sessions.map((session) => (
                <button
                  key={session.id}
                  type="button"
                  onClick={() => handleSelectSession(session)}
                  className={`chat-session-item group w-full rounded-lg px-3 py-2 text-left transition-all duration-300 ease-out ${
                    activeSessionId === session.id
                      ? 'chat-session-item--active bg-[var(--color-bg-secondary)]'
                      : 'bg-[var(--color-bg-secondary)] hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)]'
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
                        handleDeleteSession(session.id);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          event.stopPropagation();
                          handleDeleteSession(session.id);
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
          )}
        </div>
      </aside>

      <section
        className="chat-main-panel grid min-h-0 overflow-hidden rounded-xl bg-[var(--color-bg-primary)]"
        style={{
          gridTemplateColumns: selectedTeamActor
            ? 'minmax(0, 1fr) 360px'
            : 'minmax(0, 1fr) 0px',
          transition: 'grid-template-columns 360ms var(--ease-out)',
        }}
      >
        <div className="grid min-h-0 grid-rows-[auto_1fr_auto] overflow-hidden">
          <header className="px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-agent-leader)] text-white">
              <Bot size={18} strokeWidth={1.5} />
            </div>
            <div className="min-w-0">
              <div className="truncate text-base font-medium text-[var(--color-text-primary)]">
                {targetMode === 'team'
                  ? selectedTeam?.name || 'Team'
                  : runtimeAgent?.name || 'Assistant'}
              </div>
              <div className="truncate text-xs text-[var(--color-text-tertiary)]">
                {state.isStreaming
                  ? `${state.modelName || runtimeAgent?.modelConfig.primary || 'deepseek-chat'} · 正在思考...`
                  : targetMode === 'team'
                    ? `${teamLeaderAgent?.name || 'Leader'} · ${selectedTeam?.members.length || 0} 个成员`
                    : runtimeAgent?.description || 'AgentScope 2.0 · DeepSeek'}
              </div>
            </div>
            {activeSessionId && (
              <Tag size="small" color="transparent">
                已保存
              </Tag>
            )}
            {selectedModelSupportsImages && (
              <Tag size="small" color="blue">
                图片
              </Tag>
            )}
            {targetMode === 'team' && (
              <Tag size="small" color="mauve">
                Team
              </Tag>
            )}
          </div>
          </header>

          <div className="relative min-h-0 overflow-hidden">
            <div
              ref={messagesContainerRef}
              className="h-full min-h-0 overflow-auto px-5 py-4"
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
                  </div>
                ) : (
                  <>
                    {messages.map((msg) => (
                      <MessageBubble
                        key={msg.id}
                        message={msg}
                        selectedTeamActorId={selectedTeamActorId}
                        onSelectTeamActor={openTeamActorSidebar}
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
                        skillCalls={state.skillCalls}
                        hitlRequests={state.hitlRequests}
                        clarificationRequests={state.clarificationRequests}
                        plans={state.plans}
                        onSubmitClarification={handleSubmitClarification}
                        onResolveHitl={resumeInteraction}
                        selectedTeamActorId={selectedTeamActorId}
                        onSelectTeamActor={openTeamActorSidebar}
                        onFinalAnswerAnimationChange={handleFinalAnswerAnimationChange}
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

          <div className="px-5 pb-5 pt-2">
          {chatError && (
            <div className="mb-2 flex items-center gap-2 rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] px-3 py-2 text-sm text-[var(--color-error)]">
              <AlertCircle size={14} />
              {chatError}
            </div>
          )}

          {(selectingTeamWorkers || pendingTeamSelection) && (
            <TeamSelectionDock
              pending={pendingTeamSelection}
              selecting={selectingTeamWorkers}
              agents={agents}
              members={selectedTeam?.members || []}
              onConfirm={(workerIds) => void confirmTeamSelection(workerIds)}
              onCancel={() => setPendingTeamSelection(null)}
            />
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
              placeholder={state.isStreaming ? 'Agent 正在回复中...' : selectingTeamWorkers ? 'AI 正在选择 Worker...' : '输入消息...'}
              rows={2}
              disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection)}
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
                    disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection) || imageAttachments.length >= 4}
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
                      disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection)}
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
                      disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection)}
                      onClick={() => setTargetMode('team')}
                      aria-label="Team"
                      aria-pressed={targetMode === 'team'}
                    >
                      <SparkRenyizhuanrenyiAnyToanyLine size={16} />
                    </button>
                  </span>
                </Tooltip>
              </div>
              {targetMode === 'agent' ? (
                <Select
                  value={selectedAgentId}
                  onChange={(value) => setSelectedAgentId(value)}
                  options={agentOptions}
                  loading={agentsLoading}
                  disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection)}
                  style={{ width: 160 }}
                />
              ) : (
                <Select
                  value={selectedTeamId}
                  onChange={(value) => setSelectedTeamId(value)}
                  options={teamOptions}
                  loading={teamsLoading}
                  disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection)}
                  placeholder="选择 Team"
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
                        disabled={selectingTeamWorkers || Boolean(pendingTeamSelection) || (!inputValue.trim() && imageAttachments.length === 0)}
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
            opacity: selectedTeamActor ? 1 : 0,
            transform: selectedTeamActor ? 'translateX(0)' : 'translateX(16px)',
            transition: 'opacity 280ms var(--ease-out), transform 360ms var(--ease-out)',
            pointerEvents: selectedTeamActor ? 'auto' : 'none',
          }}
          aria-hidden={!selectedTeamActor}
        >
          {selectedTeamActor ? (
            <TeamActorSidebar
              actor={selectedTeamActor}
              onClose={() => setSelectedTeamActorId(null)}
            />
          ) : null}
        </div>
      </section>
    </div>
  );
}
