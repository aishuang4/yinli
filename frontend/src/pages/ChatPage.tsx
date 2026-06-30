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
import { AlertCircle, Bot, ChevronDown, ImagePlus, Plus, Send, Square, Trash2, X } from 'lucide-react';
import { Button, Empty, Select, Spinner, Tag } from '@agentscope-ai/design';
import { useEventStream } from '../hooks/useEventStream';
import { useAgents } from '../hooks/useAgents';
import { useTeams } from '../hooks/useTeams';
import { createId } from '../utils/id';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';
import { MessageBubble, StreamingBubble } from '../features/chat/ChatMessageList';
import { TeamActivitySidebar } from '../features/chat/TeamActivityViews';
import { HitlDock, isAskUserHitlRequest } from '../features/chat/HumanInteractionBlocks';
import { getPlanModeEvents, PlanModeDock } from '../features/chat/ProcessDetailsBlock';
import { TeamSelectionDock } from '../features/chat/TeamSelectionDock';
import { normalizeStoredTeamActivities, normalizeStoredToolCalls } from '../features/chat/formatters';
import type { ChatImageAttachment, ChatImagePayload, ChatSession, LocalMessage, PendingTeamSelection, StoredChatMessage } from '../features/chat/types';
import type { ChatRuntimeEvent } from '../types/chatEvents';


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
  const [selectedTeamActivityId, setSelectedTeamActivityId] = useState<string | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [resolvedPlanIds, setResolvedPlanIds] = useState<Set<string>>(() => new Set());
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);
  const recoveredRunRef = useRef<string>('');
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
      label: `${agent.name}${agent.description ? ` - ${agent.description}` : ''}`,
      value: agent.id,
    })),
  ], [agents]);

  const teamOptions = useMemo(() => (
    teams.map((team) => ({
      label: `${team.name} - ${team.members.length} 个成员`,
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
    state.teamActivities.length > 0 ||
    state.modelCaches.length > 0 ||
    state.plans.length > 0 ||
    state.runtimeEvents.length > 0
  );

  const visibleTeamActivities = useMemo(() => (
    [
      ...messages.flatMap((message) => message.teamActivities || []),
      ...state.teamActivities,
    ]
  ), [messages, state.teamActivities]);

  const selectedTeamActivity = useMemo(() => (
    visibleTeamActivities.find((activity) => activity.id === selectedTeamActivityId) || null
  ), [selectedTeamActivityId, visibleTeamActivities]);
  const approvalHitlRequests = useMemo(
    () => state.hitlRequests.filter((request) => !isAskUserHitlRequest(request)),
    [state.hitlRequests],
  );
  const conversationPlanEvents = useMemo(() => (
    state.plans.length > 0
      ? []
      : getPlanModeEvents(messages.flatMap((message) => message.runtimeEvents || []))
  ), [messages, state.plans.length]);

  const openTeamActivitySidebar = useCallback((activityId: string) => {
    setSelectedTeamActivityId(activityId);
  }, []);

  const buildTeamMembersForRun = useCallback((workerAgentIds: string[] | null) => {
    if (!selectedTeam) return [];
    if (workerAgentIds === null) return selectedTeam.members;
    const selectedWorkers = new Set(workerAgentIds);
    return selectedTeam.members.filter((member) => (
      member.role === 'leader' || selectedWorkers.has(member.agentId)
    ));
  }, [selectedTeam]);

  const buildCurrentRunOptions = useCallback((sessionId: string, approvedPlanId?: string) => ({
    sessionId,
    agentId: runtimeAgent?.id,
    agentName: runtimeAgent?.name,
    teamId: targetMode === 'team' ? selectedTeam?.id : undefined,
    teamName: targetMode === 'team' ? selectedTeam?.name : undefined,
    workerSelectorPrompt: targetMode === 'team' ? selectedTeam?.workerSelectorPrompt : undefined,
    teamMembers: targetMode === 'team'
      ? buildTeamMembersForRun(null).map((member) => ({
        id: member.id,
        agentId: member.agentId,
        name: agents.find((agent) => agent.id === member.agentId)?.name || member.name,
        role: member.role,
        description: member.description,
        executionMode: member.executionMode,
        dependsOn: member.dependsOn,
        handoffPrompt: member.handoffPrompt,
      }))
      : undefined,
    systemPrompt: runtimeAgent?.systemPrompt,
    model: runtimeAgent?.modelConfig.primary,
    skillIds: runtimeAgent?.skills,
    deepThinking,
    images: [],
    approvedPlanId,
  }), [agents, buildTeamMembersForRun, deepThinking, runtimeAgent, selectedTeam, targetMode]);

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
    setMessages(data.map((item) => ({
      id: item.id,
      role: item.role,
      content: item.content,
      createdAt: item.created_at,
      modelName: item.model_name || undefined,
      error: item.error || undefined,
      thinking: item.thinking || undefined,
      images: item.images,
      toolCalls: normalizeStoredToolCalls(item.toolCalls || item.tool_calls || []),
      skillCalls: item.skillCalls || item.skill_calls || [],
      teamActivities: normalizeStoredTeamActivities(item.teamActivities || item.team_activities || []),
      modelCaches: item.modelCaches || item.model_caches || [],
      runtimeEvents: item.runtimeEvents || item.runtime_events || [],
      clarificationRequests: item.clarificationRequests || item.clarification_requests || [],
      clarificationResolved: item.clarificationResolved || item.clarification_resolved || false,
    })));
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
    shouldAutoScrollRef.current = nearBottom;
    setShowScrollToBottom(!nearBottom);
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    messagesEndRef.current?.scrollIntoView({ behavior });
  }, []);

  /** 只有用户停留在底部附近时才自动滚动 */
  useEffect(() => {
    if (!shouldAutoScrollRef.current) return;
    scrollToBottom(state.isStreaming ? 'auto' : 'smooth');
  }, [messages, scrollToBottom, state.isStreaming, state.finalAnswerContent, state.reasoningContent, state.thinkingContent, state.toolCalls, state.skillCalls, state.teamActivities, state.modelCaches]);

  const handleNewSession = useCallback(() => {
    if (state.isStreaming) return;
    recoveredRunRef.current = '';
    setActiveSessionId(null);
    setMessages([]);
    setImageAttachments([]);
    setChatError(null);
    setSelectedTeamActivityId(null);
    setResolvedPlanIds(new Set());
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
    setSelectedTeamActivityId(null);
    setResolvedPlanIds(new Set());
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
        setSelectedTeamActivityId(null);
        setResolvedPlanIds(new Set());
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
    const visibleContent = (displayContent || content).trim();
    if (!modelContent || state.isStreaming) return;
    const sessionId = activeSessionId || createId();
    const userMessage: LocalMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: visibleContent,
      createdAt: new Date().toISOString(),
      images: [],
    };
    const clarificationPauseMessage: LocalMessage | null = state.clarificationRequests.length > 0
      ? {
        id: `assistant-clarification-${Date.now()}`,
        role: 'assistant',
        content: state.finalAnswerContent || state.textContent || '',
        createdAt: new Date().toISOString(),
        processStartedAt: state.runStartedAt,
        processCompletedAt: state.runCompletedAt || new Date().toISOString(),
        skillCalls: state.skillCalls.length > 0 ? state.skillCalls : undefined,
        toolCalls: state.toolCalls.length > 0 ? state.toolCalls : undefined,
        teamActivities: state.teamActivities.length > 0 ? state.teamActivities : undefined,
        modelCaches: state.modelCaches.length > 0 ? state.modelCaches : undefined,
        runtimeEvents: state.runtimeEvents.length > 0 ? state.runtimeEvents : undefined,
        thinking: state.thinkingContent || undefined,
        tokenUsage: state.tokenUsage,
        modelName: state.modelName,
        clarificationRequests: state.clarificationRequests,
        clarificationResolved: true,
      }
      : null;

    setActiveSessionId(sessionId);
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    setMessages((prev) => [
      ...prev,
      ...(clarificationPauseMessage ? [clarificationPauseMessage] : []),
      userMessage,
    ]);
    setChatError(null);

    await sendMessage(modelContent, {
      sessionId,
      displayMessage: visibleContent,
      agentId: runtimeAgent?.id,
      agentName: runtimeAgent?.name,
      teamId: targetMode === 'team' ? selectedTeam?.id : undefined,
      teamName: targetMode === 'team' ? selectedTeam?.name : undefined,
      workerSelectorPrompt: targetMode === 'team' ? selectedTeam?.workerSelectorPrompt : undefined,
      teamMembers: targetMode === 'team'
        ? buildTeamMembersForRun(null).map((member) => ({
          id: member.id,
          agentId: member.agentId,
          name: agents.find((agent) => agent.id === member.agentId)?.name || member.name,
          role: member.role,
          description: member.description,
          executionMode: member.executionMode,
          dependsOn: member.dependsOn,
          handoffPrompt: member.handoffPrompt,
        }))
        : undefined,
      systemPrompt: runtimeAgent?.systemPrompt,
      model: runtimeAgent?.modelConfig.primary,
      skillIds: runtimeAgent?.skills,
      deepThinking,
      images: [],
    });
  }, [
    activeSessionId,
    agents,
    buildTeamMembersForRun,
    deepThinking,
    runtimeAgent,
    selectedTeam,
    sendMessage,
    state.clarificationRequests,
    state.isStreaming,
    state.modelCaches,
    state.modelName,
    state.runCompletedAt,
    state.runStartedAt,
    state.runtimeEvents,
    state.skillCalls,
    state.teamActivities,
    state.finalAnswerContent,
    state.textContent,
    state.thinkingContent,
    state.tokenUsage,
    state.toolCalls,
    targetMode,
  ]);

  const appendPlanRuntimeEventToMessages = useCallback((planId: string, runtimeEvent: ChatRuntimeEvent) => {
    const stateHasPlan = (
      state.plans.some((plan) => plan.planId === planId) ||
      state.runtimeEvents.some((event) => String(event.payload.planId || '') === planId)
    );
    setMessages((prev) => {
      let matched = false;
      const next = prev.map((message) => {
        if (
          message.role !== 'assistant' ||
          !message.runtimeEvents?.some((event) => String(event.payload.planId || '') === planId)
        ) {
          return message;
        }
        matched = true;
        if (message.runtimeEvents.some((event) => event.id === runtimeEvent.id)) {
          return message;
        }
        return {
          ...message,
          runtimeEvents: [...message.runtimeEvents, runtimeEvent],
        };
      });
      if (!matched && stateHasPlan) {
        next.push({
          id: `assistant-plan-${Date.now()}`,
          role: 'assistant',
          content: state.finalAnswerContent || state.textContent,
          createdAt: new Date().toISOString(),
          processStartedAt: state.runStartedAt,
          processCompletedAt: state.runCompletedAt || new Date().toISOString(),
          skillCalls: state.skillCalls.length > 0 ? state.skillCalls : undefined,
          toolCalls: state.toolCalls.length > 0 ? state.toolCalls : undefined,
          teamActivities: state.teamActivities.length > 0 ? state.teamActivities : undefined,
          modelCaches: state.modelCaches.length > 0 ? state.modelCaches : undefined,
          runtimeEvents: [...state.runtimeEvents, runtimeEvent],
          thinking: state.thinkingContent || undefined,
          tokenUsage: state.tokenUsage,
          modelName: state.modelName,
          error: state.error || undefined,
        });
      }
      return next;
    });
    if (stateHasPlan) {
      reset();
    }
  }, [
    reset,
    state.error,
    state.modelCaches,
    state.modelName,
    state.plans,
    state.runCompletedAt,
    state.runStartedAt,
    state.runtimeEvents,
    state.skillCalls,
    state.teamActivities,
    state.finalAnswerContent,
    state.textContent,
    state.thinkingContent,
    state.tokenUsage,
    state.toolCalls,
  ]);

  const handleApprovePlan = useCallback(async (event: ChatRuntimeEvent) => {
    const planId = String(event.payload.planId || '');
    if (!planId || state.isStreaming) return;
    const sessionId = activeSessionId || event.sessionId || createId();
    try {
      const response = await fetch(`/api/chat/plans/${encodeURIComponent(planId)}/approve`, {
        method: 'POST',
      });
      if (response.status === 409) {
        setResolvedPlanIds((current) => new Set(current).add(planId));
        notifySuccess('计划状态已更新。');
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      setResolvedPlanIds((current) => new Set(current).add(planId));
      if (result.runtimeEvent) {
        appendPlanRuntimeEventToMessages(planId, result.runtimeEvent as ChatRuntimeEvent);
      }

      const visibleContent = '已批准计划，继续执行。';
      const modelContent = '请按已批准的计划继续执行，直接进入 Build mode 并完成计划中的任务。';
      setActiveSessionId(sessionId);
      shouldAutoScrollRef.current = true;
      setShowScrollToBottom(false);
      setMessages((prev) => [
        ...prev,
        {
          id: `user-plan-approve-${Date.now()}`,
          role: 'user',
          content: visibleContent,
          createdAt: new Date().toISOString(),
          images: [],
        },
      ]);
      setChatError(null);
      notifySuccess('计划已批准，开始执行。');
      await sendMessage(modelContent, {
        ...buildCurrentRunOptions(sessionId, planId),
        displayMessage: visibleContent,
      });
    } catch (err) {
      const message = getErrorMessage(err, '批准计划失败');
      setChatError(message);
      notifyError(message);
    }
  }, [
    activeSessionId,
    appendPlanRuntimeEventToMessages,
    buildCurrentRunOptions,
    sendMessage,
    state.isStreaming,
  ]);

  const handleDenyPlan = useCallback(async (event: ChatRuntimeEvent) => {
    const planId = String(event.payload.planId || '');
    if (!planId || state.isStreaming) return;
    try {
      const response = await fetch(`/api/chat/plans/${encodeURIComponent(planId)}/deny`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: '用户拒绝了该计划。' }),
      });
      if (response.status === 409) {
        setResolvedPlanIds((current) => new Set(current).add(planId));
        notifySuccess('计划状态已更新。');
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      setResolvedPlanIds((current) => new Set(current).add(planId));
      if (result.runtimeEvent) {
        appendPlanRuntimeEventToMessages(planId, result.runtimeEvent as ChatRuntimeEvent);
      }
      notifySuccess('计划已拒绝。');
    } catch (err) {
      const message = getErrorMessage(err, '拒绝计划失败');
      setChatError(message);
      notifyError(message);
    }
  }, [appendPlanRuntimeEventToMessages, state.isStreaming]);

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
    if (
      !state.isStreaming &&
      state.hitlRequests.length === 0 &&
      state.clarificationRequests.length === 0 &&
      (state.finalAnswerContent || state.reasoningContent || state.textContent || state.error || state.plans.length > 0 || state.runtimeEvents.length > 0) &&
      (messages.length === 0 || messages[messages.length - 1].role === 'user')
    ) {
      const assistantMessage: LocalMessage = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: state.finalAnswerContent || state.textContent,
        createdAt: new Date().toISOString(),
        processStartedAt: state.runStartedAt,
        processCompletedAt: state.runCompletedAt || new Date().toISOString(),
        skillCalls: state.skillCalls.length > 0 ? state.skillCalls : undefined,
        toolCalls: state.toolCalls.length > 0 ? state.toolCalls : undefined,
        teamActivities: state.teamActivities.length > 0 ? state.teamActivities : undefined,
        modelCaches: state.modelCaches.length > 0 ? state.modelCaches : undefined,
        runtimeEvents: state.runtimeEvents.length > 0 ? state.runtimeEvents : undefined,
        thinking: state.thinkingContent || undefined,
        tokenUsage: state.tokenUsage,
        modelName: state.modelName,
        error: state.error || undefined,
      };
      setMessages((prev) => [...prev, assistantMessage]);
      reset();
      loadSessions({ silent: true });
    }
  }, [loadSessions, messages, reset, state.clarificationRequests.length, state.error, state.finalAnswerContent, state.hitlRequests.length, state.isStreaming, state.modelCaches, state.modelName, state.plans.length, state.reasoningContent, state.runCompletedAt, state.runStartedAt, state.runtimeEvents, state.skillCalls, state.teamActivities, state.textContent, state.thinkingContent, state.tokenUsage, state.toolCalls]);

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
          gridTemplateColumns: selectedTeamActivity
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
                        selectedTeamActivityId={selectedTeamActivityId}
                        onSelectTeamActivity={openTeamActivitySidebar}
                        onSubmitClarification={handleSubmitClarification}
                      />
                    ))}

                    {hasLiveResponse && (
                      <StreamingBubble
                        textContent={state.textContent}
                        reasoningContent={state.reasoningContent}
                        finalAnswerContent={state.finalAnswerContent}
                        thinkingContent={state.thinkingContent}
                        skillCalls={state.skillCalls}
                        teamActivities={state.teamActivities}
                        modelCaches={state.modelCaches}
                        runtimeEvents={state.runtimeEvents}
                        hitlRequests={state.hitlRequests}
                        clarificationRequests={state.clarificationRequests}
                        onSubmitClarification={handleSubmitClarification}
                        onResolveHitl={resumeInteraction}
                        selectedTeamActivityId={selectedTeamActivityId}
                        onSelectTeamActivity={openTeamActivitySidebar}
                        toolCalls={state.toolCalls}
                        modelName={state.modelName}
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
            <PlanModeDock
              events={conversationPlanEvents}
              plans={state.plans}
              resolvedPlanIds={resolvedPlanIds}
              onApprovePlan={handleApprovePlan}
              onDenyPlan={handleDenyPlan}
            />
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

          {approvalHitlRequests.length > 0 && (
            <HitlDock
              requests={approvalHitlRequests}
              onResolve={resumeInteraction}
            />
          )}

          <div className="chat-composer mx-auto max-w-4xl rounded-xl bg-[var(--color-bg-secondary)] transition-shadow">
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
              rows={3}
              disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection)}
              className="block min-h-[96px] max-h-[180px] w-full resize-none rounded-t-lg border-0 bg-transparent px-4 py-3 text-base text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:bg-[var(--color-bg-tertiary)]"
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
              <div className="flex rounded-md bg-[var(--color-bg-primary)] p-1">
                <Button
                  size="small"
                  type={targetMode === 'agent' ? 'primary' : 'text'}
                  disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection)}
                  onClick={() => setTargetMode('agent')}
                >
                  单 Agent
                </Button>
                <Button
                  size="small"
                  type={targetMode === 'team' ? 'primary' : 'text'}
                  disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection)}
                  onClick={() => setTargetMode('team')}
                >
                  Team
                </Button>
              </div>
              {targetMode === 'agent' ? (
                <Select
                  value={selectedAgentId}
                  onChange={(value) => setSelectedAgentId(value)}
                  options={agentOptions}
                  loading={agentsLoading}
                  disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection)}
                  style={{ width: 260 }}
                />
              ) : (
                <Select
                  value={selectedTeamId}
                  onChange={(value) => setSelectedTeamId(value)}
                  options={teamOptions}
                  loading={teamsLoading}
                  disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection)}
                  placeholder="选择 Team"
                  style={{ width: 260 }}
                />
              )}
              <Button
                type={deepThinking ? 'primary' : 'default'}
                disabled={state.isStreaming}
                onClick={() => setDeepThinking((value) => !value)}
              >
                深度思考
              </Button>
              <div className="ml-auto flex items-center gap-2">
                <button
                  type="button"
                  className="icon-button h-8 w-8 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] text-[var(--color-text-secondary)] transition-all duration-300 ease-out hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
                  onClick={() => imageInputRef.current?.click()}
                  disabled={state.isStreaming || selectingTeamWorkers || Boolean(pendingTeamSelection) || imageAttachments.length >= 4}
                  aria-label="上传图片"
                  title="上传图片"
                >
                  <ImagePlus size={16} />
                </button>
                {state.isStreaming ? (
                  <Button danger onClick={abort}>
                    <Square size={16} />
                    停止
                  </Button>
                ) : (
                  <Button type="primary" onClick={handleSend} disabled={selectingTeamWorkers || Boolean(pendingTeamSelection) || (!inputValue.trim() && imageAttachments.length === 0)}>
                    <Send size={16} />
                    发送
                  </Button>
                )}
              </div>
            </div>
          </div>
          </div>
        </div>

        <div
          className="h-full min-h-0 min-w-0 overflow-hidden"
          style={{
            opacity: selectedTeamActivity ? 1 : 0,
            transform: selectedTeamActivity ? 'translateX(0)' : 'translateX(16px)',
            transition: 'opacity 280ms var(--ease-out), transform 360ms var(--ease-out)',
            pointerEvents: selectedTeamActivity ? 'auto' : 'none',
          }}
          aria-hidden={!selectedTeamActivity}
        >
          {selectedTeamActivity ? (
            <TeamActivitySidebar
              activity={selectedTeamActivity}
              onClose={() => setSelectedTeamActivityId(null)}
            />
          ) : null}
        </div>
      </section>
    </div>
  );
}
