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
import { Bot, Send, Square, Loader2, Wrench, ChevronDown, ChevronRight, AlertCircle, Plus, Trash2, ImagePlus, X, XCircle, BookOpenCheck, Users, CheckCircle2, Clock3, Copy, Check, GitBranch, ListChecks, Download, FileText } from 'lucide-react';
import { Button, Empty, Select, Spinner, Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../components/MarkdownContent';
import { completeOpenPlans, normalizePlanRun, useEventStream, type ClarificationQuestionInfo, type ClarificationRequestInfo, type HitlRequestInfo, type MemorySummaryInfo, type ModelCacheInfo, type PlanRunInfo, type SkillCallInfo, type SubagentCallInfo, type TeamActivityInfo, type ToolCallInfo } from '../hooks/useEventStream';
import { useAgents } from '../hooks/useAgents';
import { useTeams } from '../hooks/useTeams';
import { createId } from '../utils/id';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';
import type { Agent } from '../types/agent';
import type { TeamMember } from '../types/team';

/** 本地消息记录 */
interface LocalMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  /** 消息创建时间 */
  createdAt?: string;
  /** Agent 处理开始时间 */
  processStartedAt?: string;
  /** Agent 处理结束时间 */
  processCompletedAt?: string;
  /** 工具调用列表（仅 assistant 消息） */
  toolCalls?: ToolCallInfo[];
  /** 子 Agent 调用列表（仅 assistant 消息） */
  subagentCalls?: SubagentCallInfo[];
  /** Skill 应用列表（仅 assistant 消息） */
  skillCalls?: SkillCallInfo[];
  /** Team 协作活动（仅 assistant 消息） */
  teamActivities?: TeamActivityInfo[];
  /** 自动记忆总结 */
  memorySummaries?: MemorySummaryInfo[];
  /** 模型厂商侧 Prompt Cache 统计 */
  modelCaches?: ModelCacheInfo[];
  /** Plan Runtime 记录 */
  planRuns?: PlanRunInfo[];
  /** 思考链内容（仅 assistant 消息） */
  thinking?: string;
  /** Token 用量 */
  tokenUsage?: { input: number; output: number } | null;
  /** 使用的模型 */
  modelName?: string;
  /** 错误信息 */
  error?: string;
  /** 图片输入 */
  images?: ChatImagePayload[];
}

interface AssistantContentParts {
  finalAnswer: string;
  reactTrace: string;
}

function splitAssistantContent(content: string): AssistantContentParts {
  const text = content.trim();
  if (!text) return { finalAnswer: '', reactTrace: '' };

  const processSentences = [
    '现在让我',
    '让我先',
    '我先',
    '我继续',
    '继续查看',
    '现在查看',
    '现在进入',
    '下面开始',
  ];
  const countProcessHits = (value: string) => (
    processSentences.reduce((count, marker) => count + (value.split(marker).length - 1), 0)
  );
  const headingMatches = [...text.matchAll(/^#{1,3}\s+.*(?:报告|总结|结论|建议|方案|结果|最终|交付|完成).*$\n?/gim)];
  const lateHeading = headingMatches.find((match) => {
    const index = match.index || 0;
    if (index <= 0) return false;
    const prefix = text.slice(0, index).trim();
    return prefix.length > 280 && countProcessHits(prefix) >= 2;
  });
  const startIndex = lateHeading?.index ?? -1;
  if (startIndex > 0) {
    return {
      reactTrace: text.slice(0, startIndex).trim(),
      finalAnswer: text.slice(startIndex).trim(),
    };
  }

  const finalMarker = text.search(/(?:^|\n)(?:---\s*\n+)?(?:##\s*)?(?:✅\s*)?(?:最终|结论|总结|交付|建议|改进建议)/m);
  if (finalMarker > 0) {
    const prefix = text.slice(0, finalMarker).trim();
    if (prefix.length <= 280 || countProcessHits(prefix) < 2) {
      return { finalAnswer: text, reactTrace: '' };
    }
    return {
      reactTrace: prefix,
      finalAnswer: text.slice(finalMarker).trim(),
    };
  }

  const processHitCount = countProcessHits(text);
  if (processHitCount >= 4 && text.length > 500) {
    const midpoint = Math.floor(text.length * 0.65);
    const nextParagraph = text.indexOf('\n\n', midpoint);
    if (nextParagraph > 0) {
      return {
        reactTrace: text.slice(0, nextParagraph).trim(),
        finalAnswer: text.slice(nextParagraph).trim(),
      };
    }
  }

  return { finalAnswer: text, reactTrace: '' };
}

interface ChatImagePayload {
  name: string;
  media_type: string;
  data: string;
}

interface ChatImageAttachment extends ChatImagePayload {
  id: string;
  previewUrl: string;
}

interface PendingTeamSelection {
  message: string;
  images: ChatImagePayload[];
  selectedAgentIds: string[];
  reason: string;
}

interface ChatSession {
  id: string;
  title: string;
  agent_id: string;
  agent_name: string;
  team_id: string | null;
  team_name: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

interface StoredChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  agent_id: string | null;
  agent_name: string | null;
  model_name: string | null;
  error: string | null;
  thinking?: string;
  images: ChatImagePayload[];
  toolCalls?: ToolCallInfo[];
  tool_calls?: ToolCallInfo[];
  subagentCalls?: SubagentCallInfo[];
  subagent_calls?: SubagentCallInfo[];
  skillCalls?: SkillCallInfo[];
  skill_calls?: SkillCallInfo[];
  teamActivities?: TeamActivityInfo[];
  team_activities?: StoredTeamActivity[];
  modelCaches?: ModelCacheInfo[];
  model_caches?: ModelCacheInfo[];
  planRuns?: PlanRunInfo[];
  plan_runs?: PlanRunInfo[];
  created_at: string;
}

type StoredTeamActivity = Partial<TeamActivityInfo> & {
  agent_id?: string;
  agent_name?: string;
  tool_calls?: ToolCallInfo[];
  created_at?: string;
};

function normalizeStoredToolCalls(toolCalls: ToolCallInfo[] | undefined): ToolCallInfo[] {
  const seen = new Set<string>();
  const normalized: ToolCallInfo[] = [];

  for (const toolCall of toolCalls || []) {
    const id = String(toolCall.id || `${toolCall.name}-${normalized.length}`);
    const key = id || `${toolCall.name}-${toolCall.args}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const args = String(toolCall.args || '');
    const result = String(toolCall.result || '');
    normalized.push({
      id,
      name: normalizeStoredToolName(String(toolCall.name || 'tool'), args, result),
      args,
      result,
      status: (
        toolCall.status === 'calling' ||
        toolCall.status === 'running' ||
        toolCall.status === 'completed' ||
        toolCall.status === 'failed'
          ? toolCall.status
          : 'completed'
      ),
    });
  }

  return normalized;
}

function normalizeStoredToolName(name: string, args: string, result: string): string {
  const displayName = normalizeToolDisplayName(name);
  if (displayName !== 'tool') {
    return displayName;
  }
  const value = `${args}\n${result}`.toLowerCase();
  if (
    value.includes('补充信息') ||
    value.includes('用户拒绝') ||
    value.includes('human input') ||
    value.includes('askuser') ||
    value.includes('"questions"')
  ) {
    return 'AskUser';
  }
  return displayName;
}

function normalizeToolDisplayName(name: string): string {
  const value = String(name || 'tool');
  if (value.startsWith('CallAgent_')) {
    const agentName = value.slice('CallAgent_'.length).replace(/_/g, ' ').trim() || '子 Agent';
    return `子 Agent：${agentName}`;
  }
  const parts = value.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return value;
}

function normalizeStoredTeamActivities(activities: StoredTeamActivity[]): TeamActivityInfo[] {
  return activities.map((activity) => ({
    id: String(activity.id || createId()),
    agentId: String(activity.agentId || activity.agent_id || ''),
    agentName: String(activity.agentName || activity.agent_name || 'Agent'),
    role: String(activity.role || 'worker'),
    status: (
      activity.status === 'pending' ||
      activity.status === 'running' ||
      activity.status === 'waiting' ||
      activity.status === 'completed' ||
      activity.status === 'failed'
        ? activity.status
        : 'completed'
    ),
    task: String(activity.task || ''),
    detail: String(activity.detail || ''),
    output: String(activity.output || ''),
    thinking: String(activity.thinking || ''),
    currentEvent: String(activity.currentEvent || ''),
    toolCalls: normalizeStoredToolCalls(activity.toolCalls || activity.tool_calls || []),
    executionMode: String(activity.executionMode || 'parallel'),
    dependsOn: activity.dependsOn || [],
    waitReason: String(activity.waitReason || ''),
    dependencyOutputCount: Number(activity.dependencyOutputCount || 0),
    leaderContextCount: Number(activity.leaderContextCount || 0),
    createdAt: String(activity.createdAt || activity.created_at || ''),
  }));
}

function normalizeStoredPlanRuns(planRuns: PlanRunInfo[] | undefined): PlanRunInfo[] {
  return (planRuns || []).map((plan) => normalizePlanRun(plan as unknown as Record<string, unknown>));
}

/**
 * ChatPage 组件
 */
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
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [selectedSubagentCallId, setSelectedSubagentCallId] = useState<string | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);
  const { state, sendMessage, resumeInteraction, abort, reset } = useEventStream();
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
    Boolean(state.textContent) ||
    Boolean(state.thinkingContent) ||
    state.toolCalls.length > 0 ||
    state.subagentCalls.length > 0 ||
    state.skillCalls.length > 0 ||
    state.teamActivities.length > 0 ||
    state.memorySummaries.length > 0 ||
    state.modelCaches.length > 0 ||
    state.planRuns.length > 0
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

  const visiblePlanRuns = useMemo(() => (
    [
      ...messages.flatMap((message) => message.planRuns || []),
      ...state.planRuns,
    ]
  ), [messages, state.planRuns]);

  const selectedPlan = useMemo(() => (
    visiblePlanRuns.find((plan) => plan.id === selectedPlanId) || null
  ), [selectedPlanId, visiblePlanRuns]);

  const visibleSubagentCalls = useMemo(() => (
    [
      ...messages.flatMap((message) => message.subagentCalls || []),
      ...state.subagentCalls,
    ].filter((toolCall) => isSubagentToolName(toolCall.name))
  ), [messages, state.subagentCalls]);

  const selectedSubagentCall = useMemo(() => (
    visibleSubagentCalls.find((toolCall) => toolCall.id === selectedSubagentCallId) || null
  ), [selectedSubagentCallId, visibleSubagentCalls]);

  const openPlanSidebar = useCallback((planId: string) => {
    setSelectedTeamActivityId(null);
    setSelectedSubagentCallId(null);
    setSelectedPlanId(planId);
  }, []);

  const openTeamActivitySidebar = useCallback((activityId: string) => {
    setSelectedPlanId(null);
    setSelectedSubagentCallId(null);
    setSelectedTeamActivityId(activityId);
  }, []);

  const openSubagentSidebar = useCallback((toolCallId: string) => {
    setSelectedTeamActivityId(null);
    setSelectedPlanId(null);
    setSelectedSubagentCallId(toolCallId);
  }, []);

  const buildTeamMembersForRun = useCallback((workerAgentIds: string[] | null) => {
    if (!selectedTeam) return [];
    if (workerAgentIds === null) return selectedTeam.members;
    const selectedWorkers = new Set(workerAgentIds);
    return selectedTeam.members.filter((member) => (
      member.role === 'leader' || selectedWorkers.has(member.agentId)
    ));
  }, [selectedTeam]);

  const loadSessions = useCallback(async () => {
    try {
      setSessionsLoading(true);
      const response = await fetch('/api/chat/sessions');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data: ChatSession[] = await response.json();
      setSessions(data);
    } finally {
      setSessionsLoading(false);
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
      subagentCalls: normalizeStoredToolCalls(item.subagentCalls || item.subagent_calls || item.toolCalls || item.tool_calls || []).filter((toolCall) => isSubagentToolName(toolCall.name)),
      skillCalls: item.skillCalls || item.skill_calls || [],
      teamActivities: normalizeStoredTeamActivities(item.teamActivities || item.team_activities || []),
      modelCaches: item.modelCaches || item.model_caches || [],
      planRuns: item.role === 'assistant' && item.content
        ? completeOpenPlans(normalizeStoredPlanRuns(item.planRuns || item.plan_runs || []), item.created_at)
        : normalizeStoredPlanRuns(item.planRuns || item.plan_runs || []),
    })));
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

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
  }, [messages, scrollToBottom, state.isStreaming, state.textContent, state.thinkingContent, state.toolCalls, state.subagentCalls, state.skillCalls, state.teamActivities, state.memorySummaries, state.modelCaches, state.planRuns]);

  const handleNewSession = useCallback(() => {
    if (state.isStreaming) return;
    setActiveSessionId(null);
    setMessages([]);
    setImageAttachments([]);
    setChatError(null);
    setSelectedTeamActivityId(null);
    setSelectedPlanId(null);
    setSelectedSubagentCallId(null);
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    reset();
  }, [reset, state.isStreaming]);

  const handleSelectSession = useCallback(async (session: ChatSession) => {
    if (state.isStreaming) return;
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
    setSelectedPlanId(null);
    setSelectedSubagentCallId(null);
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
        setActiveSessionId(null);
        setMessages([]);
        setImageAttachments([]);
        setChatError(null);
        setSelectedTeamActivityId(null);
        setSelectedPlanId(null);
        setSelectedSubagentCallId(null);
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

  const handleSubmitClarification = useCallback(async (content: string) => {
    if (!content.trim() || state.isStreaming) return;
    const sessionId = activeSessionId || createId();
    const userMessage: LocalMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
      images: [],
    };

    setActiveSessionId(sessionId);
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    setMessages((prev) => [...prev, userMessage]);
    setChatError(null);

    await sendMessage(content, {
      sessionId,
      agentId: runtimeAgent?.id,
      agentName: runtimeAgent?.name,
      teamId: targetMode === 'team' ? selectedTeam?.id : undefined,
      teamName: targetMode === 'team' ? selectedTeam?.name : undefined,
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
  }, [activeSessionId, agents, buildTeamMembersForRun, deepThinking, runtimeAgent, selectedTeam, sendMessage, state.isStreaming, targetMode]);

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
      (state.textContent || state.error) &&
      (messages.length === 0 || messages[messages.length - 1].role === 'user')
    ) {
      const regularToolCalls = state.toolCalls.filter((toolCall) => !isSubagentToolName(toolCall.name));
      const assistantMessage: LocalMessage = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: state.textContent,
        createdAt: new Date().toISOString(),
        processStartedAt: state.runStartedAt,
        processCompletedAt: state.runCompletedAt || new Date().toISOString(),
        skillCalls: state.skillCalls.length > 0 ? state.skillCalls : undefined,
        toolCalls: regularToolCalls.length > 0 ? regularToolCalls : undefined,
        subagentCalls: state.subagentCalls.length > 0 ? state.subagentCalls : undefined,
        teamActivities: state.teamActivities.length > 0 ? state.teamActivities : undefined,
        memorySummaries: state.memorySummaries.length > 0 ? state.memorySummaries : undefined,
        modelCaches: state.modelCaches.length > 0 ? state.modelCaches : undefined,
        planRuns: state.planRuns.length > 0 ? state.planRuns : undefined,
        thinking: state.thinkingContent || undefined,
        tokenUsage: state.tokenUsage,
        modelName: state.modelName,
        error: state.error || undefined,
      };
      setMessages((prev) => [...prev, assistantMessage]);
      reset();
      loadSessions();
    }
  }, [loadSessions, messages, reset, state.error, state.hitlRequests.length, state.isStreaming, state.memorySummaries, state.modelCaches, state.modelName, state.planRuns, state.runCompletedAt, state.runStartedAt, state.skillCalls, state.subagentCalls, state.teamActivities, state.textContent, state.thinkingContent, state.tokenUsage, state.toolCalls]);

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
          gridTemplateColumns: selectedTeamActivity || selectedPlan || selectedSubagentCall
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
                        onOpenSubagent={openSubagentSidebar}
                        onOpenPlan={openPlanSidebar}
                        onSubmitClarification={handleSubmitClarification}
                      />
                    ))}

                    {hasLiveResponse && (
                      <StreamingBubble
                        textContent={state.textContent}
                        thinkingContent={state.thinkingContent}
                        skillCalls={state.skillCalls}
                        teamActivities={state.teamActivities}
                        memorySummaries={state.memorySummaries}
                        modelCaches={state.modelCaches}
                        planRuns={state.planRuns}
                        hitlRequests={state.hitlRequests}
                        onResolveHitl={resumeInteraction}
                        selectedTeamActivityId={selectedTeamActivityId}
                        onSelectTeamActivity={openTeamActivitySidebar}
                        onOpenSubagent={openSubagentSidebar}
                        onOpenPlan={openPlanSidebar}
                        toolCalls={state.toolCalls}
                        subagentCalls={state.subagentCalls}
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

          {state.hitlRequests.length > 0 && (
            <HitlDock
              requests={state.hitlRequests}
              onResolve={resumeInteraction}
            />
          )}

          {state.clarificationRequests.length > 0 && (
            <ClarificationDock
              requests={state.clarificationRequests}
              onSubmit={handleSubmitClarification}
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
            opacity: selectedTeamActivity || selectedPlan || selectedSubagentCall ? 1 : 0,
            transform: selectedTeamActivity || selectedPlan || selectedSubagentCall ? 'translateX(0)' : 'translateX(16px)',
            transition: 'opacity 280ms var(--ease-out), transform 360ms var(--ease-out)',
            pointerEvents: selectedTeamActivity || selectedPlan || selectedSubagentCall ? 'auto' : 'none',
          }}
          aria-hidden={!selectedTeamActivity && !selectedPlan && !selectedSubagentCall}
        >
          {selectedTeamActivity ? (
            <TeamActivitySidebar
              activity={selectedTeamActivity}
              onClose={() => setSelectedTeamActivityId(null)}
            />
          ) : selectedPlan ? (
            <PlanRuntimeSidebar
              plan={selectedPlan}
              onClose={() => setSelectedPlanId(null)}
            />
          ) : selectedSubagentCall ? (
            <SubagentCallSidebar
              toolCall={selectedSubagentCall}
              onClose={() => setSelectedSubagentCallId(null)}
            />
          ) : null}
        </div>
      </section>
    </div>
  );
}

/**
 * MessageBubble — 历史消息气泡（已完成的消息）
 */
function extractClarificationFromText(content: string): ClarificationRequestInfo | null {
  const match = content.match(/<clarification>\s*([\s\S]*?)\s*<\/clarification>/);
  const rawText = match?.[1] ?? extractRawClarificationJson(content);
  if (!rawText) return null;

  try {
    const raw = JSON.parse(rawText);
    const questions = Array.isArray(raw.questions)
      ? raw.questions
        .map((question: Record<string, unknown>, index: number) => {
          const type = String(question.type || '');
          if (!['choice', 'multi_choice', 'text'].includes(type)) return null;
          const id = String(question.id || `q${index + 1}`);
          const label = String(question.label || question.question || '');
          if (!id || !label) return null;
          const options = Array.isArray(question.options)
            ? question.options.map((option) => String(option).trim()).filter(Boolean).slice(0, 5)
            : [];
          if ((type === 'choice' || type === 'multi_choice') && options.length < 2) return null;
          return {
            id,
            type: type as 'choice' | 'multi_choice' | 'text',
            label,
            options,
            placeholder: String(question.placeholder || ''),
            required: question.required !== false,
          };
        })
        .filter(Boolean)
      : [];
    if (questions.length === 0) return null;
    return {
      id: `clarification-${Date.now()}`,
      title: String(raw.title || '需要补充信息'),
      description: String(raw.description || ''),
      questions: questions as ClarificationRequestInfo['questions'],
      createdAt: new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

function extractRawClarificationJson(content: string): string | null {
  if (!content.includes('"title"') || !content.includes('"questions"')) return null;
  const start = content.indexOf('{');
  const end = content.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  return content.slice(start, end + 1);
}

function MessageBubble({
  message,
  selectedTeamActivityId,
  onSelectTeamActivity,
  onOpenSubagent,
  onOpenPlan,
  onSubmitClarification,
}: {
  message: LocalMessage;
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  onOpenSubagent: (toolCallId: string) => void;
  onOpenPlan: (planId: string) => void;
  onSubmitClarification: (content: string) => Promise<void>;
}) {
  const isUser = message.role === 'user';
  const clarification = !isUser ? extractClarificationFromText(message.content) : null;
  const contentParts = !isUser && !clarification
    ? splitAssistantContent(message.content)
    : { finalAnswer: message.content, reactTrace: '' };
  const generatedFiles = !isUser
    ? collectGeneratedFiles(message.toolCalls || [], message.teamActivities || [])
    : [];
  const hasProcessDetails = !isUser && Boolean(
    contentParts.reactTrace ||
    message.thinking ||
    (message.teamActivities && message.teamActivities.length > 0) ||
    (message.skillCalls && message.skillCalls.length > 0) ||
    (message.memorySummaries && message.memorySummaries.length > 0) ||
    (message.modelCaches && message.modelCaches.length > 0) ||
    (message.subagentCalls && message.subagentCalls.length > 0) ||
    (message.toolCalls && message.toolCalls.length > 0)
  );

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: isUser ? 'row-reverse' : 'row',
        alignItems: 'flex-start',
        contentVisibility: 'auto',
        containIntrinsicSize: isUser ? '88px' : '220px',
      }}
    >
      {/* 消息内容 */}
      <div
        style={{
          width: isUser ? 'auto' : '100%',
          maxWidth: isUser ? '75%' : '100%',
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
        }}
      >
        {/* 错误提示 */}
        {message.error && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '8px 12px',
              borderRadius: 'var(--radius-lg)',
              backgroundColor: 'var(--color-error-bg)',
              color: 'var(--color-error)',
              fontSize: 'var(--text-sm)',
            }}
          >
            <AlertCircle size={14} />
            {message.error}
          </div>
        )}

        {message.images && message.images.length > 0 && (
          <div className={`flex flex-wrap gap-2 ${isUser ? 'justify-end' : 'justify-start'}`}>
            {message.images.map((image, index) => (
              <img
                key={`${image.name}-${index}`}
                src={`data:${image.media_type};base64,${image.data}`}
                alt={image.name || `image-${index + 1}`}
                className="max-h-48 max-w-64 rounded-md border border-[var(--color-border)] object-contain"
              />
            ))}
          </div>
        )}

        {hasProcessDetails && (
          <ProcessDetailsBlock
            defaultExpanded={false}
            hasFinalAnswer={Boolean(message.content && !clarification)}
            processStartedAt={message.processStartedAt}
            processCompletedAt={message.processCompletedAt}
            reactTraceContent={contentParts.reactTrace}
            thinkingContent={message.thinking || ''}
            teamActivities={message.teamActivities || []}
            skillCalls={message.skillCalls || []}
            memorySummaries={message.memorySummaries || []}
            modelCaches={message.modelCaches || []}
            toolCalls={message.toolCalls || []}
            subagentCalls={message.subagentCalls || []}
            selectedTeamActivityId={selectedTeamActivityId}
            onSelectTeamActivity={onSelectTeamActivity}
            onOpenSubagent={onOpenSubagent}
          />
        )}

        {!isUser && message.planRuns && message.planRuns.length > 0 && (
          <PlanOpenButton plan={message.planRuns[message.planRuns.length - 1]} onOpen={onOpenPlan} />
        )}

        {/* 文本内容 */}
        {clarification && (
          <ClarificationBlock
            request={clarification}
            onSubmit={onSubmitClarification}
          />
        )}

        {contentParts.finalAnswer && !clarification && (
          <div
            className={`chat-message-content ${isUser ? 'chat-message-content--user' : 'chat-message-content--assistant'}`}
            style={{
              padding: '10px 14px',
              borderRadius: 'var(--radius-xl)',
              backgroundColor: isUser
                ? 'var(--color-accent)'
                : 'var(--color-bg-secondary)',
              color: isUser
                ? 'var(--color-text-inverse)'
                : 'var(--color-text-primary)',
              fontSize: 'var(--text-base)',
              lineHeight: 'var(--leading-normal)',
              wordBreak: 'break-word',
              whiteSpace: isUser ? 'pre-wrap' : undefined,
            }}
          >
            {isUser ? message.content : <MarkdownContent content={contentParts.finalAnswer} />}
          </div>
        )}

        {!isUser && generatedFiles.length > 0 && (
          <GeneratedFilesBlock files={generatedFiles} />
        )}

        {!isUser && contentParts.finalAnswer && !clarification && (
          <MessageToolbar markdown={contentParts.finalAnswer} createdAt={message.createdAt} />
        )}

        {/* Token 用量 */}
        {message.tokenUsage && (
          <div
            style={{
              fontSize: 'var(--text-xs)',
              color: 'var(--color-text-tertiary)',
              paddingLeft: '4px',
            }}
          >
            {message.modelName && `${message.modelName} · `}
            {message.tokenUsage.input} + {message.tokenUsage.output} tokens
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * StreamingBubble — 正在流式生成的回复气泡
 *
 * 实时显示 Agent 正在生成的内容，包括：
 * - 思考链（thinking）
 * - 工具调用（tool calls）
 * - 文本回复（text delta）
 */
function StreamingBubble({
  textContent,
  thinkingContent,
  skillCalls,
  teamActivities,
  memorySummaries,
  modelCaches,
  planRuns,
  hitlRequests,
  onResolveHitl,
  selectedTeamActivityId,
  onSelectTeamActivity,
  onOpenSubagent,
  onOpenPlan,
  toolCalls,
  subagentCalls,
  modelName,
  runStartedAt,
  runCompletedAt,
}: {
  textContent: string;
  thinkingContent: string;
  skillCalls: SkillCallInfo[];
  teamActivities: TeamActivityInfo[];
  memorySummaries: MemorySummaryInfo[];
  modelCaches: ModelCacheInfo[];
  planRuns: PlanRunInfo[];
  hitlRequests: HitlRequestInfo[];
  onResolveHitl: (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  onOpenSubagent: (toolCallId: string) => void;
  onOpenPlan: (planId: string) => void;
  toolCalls: ToolCallInfo[];
  subagentCalls: SubagentCallInfo[];
  modelName: string;
  runStartedAt: string;
  runCompletedAt: string;
}) {
  const generatedFiles = collectGeneratedFiles(toolCalls, teamActivities);
  const contentParts = splitAssistantContent(textContent);
  const hasProcessDetails = Boolean(
    contentParts.reactTrace ||
    thinkingContent ||
    teamActivities.length > 0 ||
    skillCalls.length > 0 ||
    memorySummaries.length > 0 ||
    modelCaches.length > 0 ||
    subagentCalls.length > 0 ||
    toolCalls.length > 0
  );

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
      }}
    >
      {/* 内容区域 */}
      <div
        style={{
          width: '100%',
          maxWidth: '100%',
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
        }}
      >
        {hasProcessDetails && (
          <ProcessDetailsBlock
            defaultExpanded={!textContent}
            hasFinalAnswer={Boolean(contentParts.finalAnswer)}
            processStartedAt={runStartedAt}
            processCompletedAt={runCompletedAt}
            reactTraceContent={contentParts.reactTrace}
            thinkingContent={thinkingContent}
            teamActivities={teamActivities}
            skillCalls={skillCalls}
            memorySummaries={memorySummaries}
            modelCaches={modelCaches}
            toolCalls={toolCalls}
            subagentCalls={subagentCalls}
            selectedTeamActivityId={selectedTeamActivityId}
            onSelectTeamActivity={onSelectTeamActivity}
            onOpenSubagent={onOpenSubagent}
          />
        )}

        {planRuns.length > 0 && (
          <PlanOpenButton plan={planRuns[planRuns.length - 1]} onOpen={onOpenPlan} />
        )}

        {hitlRequests.map((request) => (
          <HitlApprovalBlock
            key={request.interactionId}
            request={request}
            onResolve={onResolveHitl}
          />
        ))}

        {/* 文本内容 */}
        {contentParts.finalAnswer && (
          <div
            className="chat-message-content chat-message-content--assistant"
            style={{
              padding: '10px 14px',
              borderRadius: 'var(--radius-xl)',
              backgroundColor: 'var(--color-bg-secondary)',
              color: 'var(--color-text-primary)',
              fontSize: 'var(--text-base)',
              lineHeight: 'var(--leading-normal)',
              wordBreak: 'break-word',
            }}
          >
            <MarkdownContent content={contentParts.finalAnswer} streaming />
            <span
              style={{
                display: 'inline-block',
                width: '2px',
                height: '14px',
                backgroundColor: 'var(--color-accent)',
                marginLeft: '2px',
                verticalAlign: 'text-bottom',
                animation: 'blink 1s infinite',
              }}
            />
          </div>
        )}

        {generatedFiles.length > 0 && (
          <GeneratedFilesBlock files={generatedFiles} />
        )}

        {contentParts.finalAnswer && (
          <MessageToolbar markdown={contentParts.finalAnswer} createdAt={new Date().toISOString()} />
        )}

        {/* 无内容时显示加载指示器 */}
        {!contentParts.finalAnswer && generatedFiles.length === 0 && toolCalls.length === 0 && subagentCalls.length === 0 && skillCalls.length === 0 && teamActivities.length === 0 && memorySummaries.length === 0 && modelCaches.length === 0 && hitlRequests.length === 0 && !thinkingContent && !contentParts.reactTrace && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '10px 14px',
              color: 'var(--color-text-tertiary)',
              fontSize: 'var(--text-sm)',
            }}
          >
            <Loader2 size={14} className="animate-spin" />
            {modelName ? `${modelName} 正在思考...` : '正在连接 Agent...'}
          </div>
        )}
      </div>
    </div>
  );
}

async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
}

function formatMessageTime(value?: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatProcessDuration(startedAt?: string, completedAt?: string, fallbackNowMs = Date.now()): string {
  if (!startedAt) return '';
  const startMs = new Date(startedAt).getTime();
  const endMs = completedAt ? new Date(completedAt).getTime() : fallbackNowMs;
  if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs < startMs) return '';

  const totalSeconds = Math.max(1, Math.round((endMs - startMs) / 1000));
  if (totalSeconds < 60) {
    return `${totalSeconds} 秒`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds > 0 ? `${minutes} 分 ${seconds} 秒` : `${minutes} 分`;
}

function MessageToolbar({ markdown, createdAt }: { markdown: string; createdAt?: string }) {
  const [copied, setCopied] = useState(false);
  const messageTime = formatMessageTime(createdAt);

  const handleCopyMarkdown = useCallback(async () => {
    if (!markdown.trim()) return;
    await copyTextToClipboard(markdown);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }, [markdown]);

  return (
    <div className="flex items-center justify-between gap-2 px-1">
      <Button
        size="small"
        type="text"
        className="h-7 px-2 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)]"
        onClick={handleCopyMarkdown}
        disabled={!markdown.trim()}
        aria-label="复制文本"
      >
        {copied ? <Check size={13} /> : <Copy size={13} />}
        <span>{copied ? '已复制' : '复制文本'}</span>
      </Button>
      {messageTime && (
        <time
          dateTime={createdAt}
          className="shrink-0 text-xs text-[var(--color-text-tertiary)]"
        >
          {messageTime}
        </time>
      )}
    </div>
  );
}

/**
 * TeamActivityPanel — Team 协作过程展示
 */
function TeamActivityPanel({
  activities,
  selectedActivityId,
  onSelectActivity,
}: {
  activities: TeamActivityInfo[];
  selectedActivityId: string | null;
  onSelectActivity: (id: string) => void;
}) {
  const leader = activities.find((item) => item.role === 'leader');
  const workers = activities.filter((item) => item.role !== 'leader');
  const completedCount = activities.filter((item) => item.status === 'completed').length;

  return (
    <div className="w-full overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Users size={14} color="var(--color-agent-leader)" />
          <span>Team 协作</span>
        </div>
        <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
          {completedCount}/{activities.length} 完成
        </span>
      </div>

      {workers.length > 0 && (
        <TeamExecutionGraph
          leader={leader || null}
          workers={workers}
          selectedActivityId={selectedActivityId}
          onSelectActivity={onSelectActivity}
        />
      )}

      <div className="grid gap-2 px-3 pb-3 md:grid-cols-2">
        {leader && (
          <TeamActivityCard
            activity={leader}
            selected={selectedActivityId === leader.id}
            onSelect={() => onSelectActivity(leader.id)}
          />
        )}
        {workers.map((activity) => (
          <TeamActivityCard
            key={activity.id}
            activity={activity}
            selected={selectedActivityId === activity.id}
            onSelect={() => onSelectActivity(activity.id)}
          />
        ))}
      </div>
    </div>
  );
}

function TeamExecutionGraph({
  leader,
  workers,
  selectedActivityId,
  onSelectActivity,
}: {
  leader: TeamActivityInfo | null;
  workers: TeamActivityInfo[];
  selectedActivityId: string | null;
  onSelectActivity: (id: string) => void;
}) {
  const nameByAgentId = new Map(workers.map((worker) => [worker.agentId, worker.agentName]));
  const parallelCount = workers.filter((worker) => worker.executionMode !== 'depends_on').length;
  const waitingCount = workers.filter((worker) => worker.status === 'pending' && worker.waitReason).length;

  return (
    <div className="px-3 pb-3">
      <div className="rounded-md bg-[var(--color-bg-primary)] p-3">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs font-medium text-[var(--color-text-secondary)]">
            <GitBranch size={13} color="var(--color-accent)" />
            <span>执行图</span>
          </div>
          <div className="flex flex-wrap gap-1">
            <Tag size="small" color="transparent">并行 {parallelCount}</Tag>
            <Tag size="small" color={waitingCount > 0 ? 'orange' : 'green'}>等待 {waitingCount}</Tag>
            {leader && leader.leaderContextCount > 0 && (
              <Tag size="small" color="blue">Leader 使用 {leader.leaderContextCount} 个输出</Tag>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-stretch gap-2">
          {workers.map((worker) => {
            const dependencyNames = worker.dependsOn.map((dependency) => nameByAgentId.get(dependency) || dependency);
            const selected = selectedActivityId === worker.id;
            const statusColor = worker.status === 'completed'
              ? 'var(--color-success)'
              : worker.status === 'failed'
                ? 'var(--color-error)'
                : worker.status === 'running'
                  ? 'var(--color-accent)'
                  : 'var(--color-text-tertiary)';
            return (
              <button
                key={worker.id}
                type="button"
                onClick={() => onSelectActivity(worker.id)}
                className={`ui-card-transition min-w-[180px] max-w-[260px] flex-1 rounded-md border bg-[var(--color-bg-secondary)] p-2 text-left ${
                  selected ? 'border-[var(--color-accent)] shadow-sm' : 'border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 truncate text-xs font-medium text-[var(--color-text-primary)]">
                    {worker.agentName}
                  </div>
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: statusColor }} />
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  <Tag size="small" color={worker.executionMode === 'depends_on' ? 'blue' : 'transparent'}>
                    {worker.executionMode === 'depends_on' ? '依赖执行' : '并行'}
                  </Tag>
                  {worker.dependencyOutputCount > 0 && (
                    <Tag size="small" color="green">收到 {worker.dependencyOutputCount}</Tag>
                  )}
                </div>
                {dependencyNames.length > 0 && (
                  <div className="mt-2 text-[11px] text-[var(--color-text-tertiary)]">
                    依赖：{dependencyNames.join(' -> ')}
                  </div>
                )}
                {(worker.waitReason || worker.detail) && (
                  <div className="mt-1 line-clamp-2 text-[11px] text-[var(--color-text-tertiary)]">
                    {worker.waitReason || worker.detail}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function TeamActivityCard({
  activity,
  selected,
  onSelect,
}: {
  activity: TeamActivityInfo;
  selected: boolean;
  onSelect: () => void;
}) {
  const toolCalls = normalizeStoredToolCalls(activity.toolCalls || []);
  const isRunning = activity.status === 'running';
  const isWaiting = activity.status === 'waiting';
  const isCompleted = activity.status === 'completed';
  const isFailed = activity.status === 'failed';
  const color = isFailed
    ? 'var(--color-error)'
    : isCompleted
      ? 'var(--color-success)'
      : isRunning || isWaiting
        ? 'var(--color-accent)'
        : 'var(--color-text-tertiary)';

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`ui-card-transition min-h-[132px] rounded-md bg-[var(--color-bg-primary)] p-3 text-left ${
        selected ? 'ring-1 ring-[var(--color-accent)]' : 'hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)]'
      }`}
    >
      <div className="mb-2 flex w-full items-center gap-2 text-left">
        <div
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-white"
          style={{
            backgroundColor: activity.role === 'leader'
              ? 'var(--color-agent-leader)'
              : 'var(--color-agent-worker)',
          }}
        >
          <Bot size={13} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
            {activity.agentName}
          </div>
          <div className="text-xs text-[var(--color-text-tertiary)]">
            {activity.role === 'leader' ? 'Leader' : 'Worker'}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1 text-xs" style={{ color }}>
          {isRunning ? (
            <Loader2 size={12} className="animate-spin" />
          ) : isWaiting ? (
            <AlertCircle size={12} />
          ) : isCompleted ? (
            <CheckCircle2 size={12} />
          ) : (
            <Clock3 size={12} />
          )}
          <span>{getTeamActivityStatusText(activity.status)}</span>
        </div>
        <ChevronRight size={12} />
      </div>
      <div className="line-clamp-2 text-sm text-[var(--color-text-secondary)]">
        {activity.task}
      </div>
      {activity.currentEvent && (
        <div className="mt-1 flex items-center gap-1 text-xs text-[var(--color-accent)]">
          {isRunning && <Loader2 size={11} className="animate-spin" />}
          <span className="truncate">{activity.currentEvent}</span>
        </div>
      )}
      {activity.detail && (
        <div className="mt-1 line-clamp-2 text-xs text-[var(--color-text-tertiary)]">
          {activity.detail}
        </div>
      )}
      {toolCalls.length > 0 && (
        <div className="mt-2 flex min-w-0 items-center gap-1 overflow-hidden">
          {toolCalls.slice(0, 2).map((toolCall) => (
            <span
              key={toolCall.id}
              className="min-w-0 max-w-[160px] truncate rounded border border-[var(--color-border)] px-1.5 py-0.5 text-xs text-[var(--color-text-tertiary)]"
              title={toolCall.name}
            >
              {toolCall.name}
            </span>
          ))}
          {toolCalls.length > 2 && (
            <span className="shrink-0 rounded border border-[var(--color-border)] px-1.5 py-0.5 text-xs text-[var(--color-text-tertiary)]">
              +{toolCalls.length - 2}
            </span>
          )}
        </div>
      )}
    </button>
  );
}

function TeamActivitySidebar({
  activity,
  onClose,
}: {
  activity: TeamActivityInfo;
  onClose: () => void;
}) {
  const toolCalls = normalizeStoredToolCalls(activity.toolCalls || []);
  const isRunning = activity.status === 'running';
  const isWaiting = activity.status === 'waiting';

  return (
    <aside className="flex h-full min-h-0 flex-col bg-[var(--color-bg-secondary)] shadow-inner">
      <div className="flex items-start gap-3 px-4 py-4">
        <div
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white"
          style={{
            backgroundColor: activity.role === 'leader'
              ? 'var(--color-agent-leader)'
              : 'var(--color-agent-worker)',
          }}
        >
          <Bot size={16} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-base font-medium text-[var(--color-text-primary)]">
            {activity.agentName}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
            <span>{activity.role === 'leader' ? 'Leader' : 'Worker'}</span>
            <span>{getTeamActivityStatusText(activity.status)}</span>
          </div>
        </div>
        <button
          type="button"
          className="icon-button h-8 w-8 rounded-md text-[var(--color-text-secondary)] transition-all duration-300 ease-out hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
          onClick={onClose}
          aria-label="关闭详情"
          title="关闭详情"
        >
          <X size={14} />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 pb-4">
        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">当前动作</div>
          <div className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
            {isRunning && <Loader2 size={13} className="animate-spin text-[var(--color-accent)]" />}
            {isWaiting && <AlertCircle size={13} className="text-[var(--color-warning)]" />}
            <span>{activity.currentEvent || getTeamActivityStatusText(activity.status)}</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            <Tag size="small" color={activity.executionMode === 'depends_on' ? 'blue' : 'transparent'}>
              {activity.role === 'leader'
                ? 'Leader 汇总'
                : activity.executionMode === 'depends_on'
                  ? '依赖执行'
                  : '并行执行'}
            </Tag>
            {activity.dependencyOutputCount > 0 && (
              <Tag size="small" color="green">上游输出 {activity.dependencyOutputCount}</Tag>
            )}
            {activity.leaderContextCount > 0 && (
              <Tag size="small" color="blue">Worker 输出 {activity.leaderContextCount}</Tag>
            )}
          </div>
          {activity.waitReason && (
            <div className="mt-2 text-xs text-[var(--color-warning)]">
              {activity.waitReason}
            </div>
          )}
        </section>

        {activity.dependsOn.length > 0 && (
          <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
            <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">依赖 Worker</div>
            <div className="flex flex-wrap gap-1">
              {activity.dependsOn.map((dependency) => (
                <Tag key={dependency} size="small" color="transparent">
                  {dependency}
                </Tag>
              ))}
            </div>
          </section>
        )}

        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">任务</div>
          <div className="whitespace-pre-wrap break-words text-sm text-[var(--color-text-secondary)]">
            {activity.task}
          </div>
          {activity.detail && (
            <div className="mt-2 whitespace-pre-wrap break-words text-xs text-[var(--color-text-tertiary)]">
              {activity.detail}
            </div>
          )}
        </section>

        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">输出</div>
          {activity.output ? (
            <div className="max-h-80 overflow-auto text-xs text-[var(--color-text-secondary)]">
              <MarkdownContent content={activity.output} />
            </div>
          ) : (
            <div className="text-sm text-[var(--color-text-tertiary)]">暂无输出</div>
          )}
        </section>

        {activity.thinking && (
          <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
            <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">思考</div>
            <div className="max-h-60 overflow-auto text-xs text-[var(--color-text-tertiary)]">
              <MarkdownContent content={activity.thinking} />
            </div>
          </section>
        )}

        {toolCalls.length > 0 && (
          <section className="space-y-2 rounded-lg bg-[var(--color-bg-primary)] p-3">
            <div className="text-xs text-[var(--color-text-tertiary)]">工具调用</div>
            <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
              {toolCalls.map((toolCall) => (
                <ToolCallBlock key={toolCall.id} toolCall={toolCall} />
              ))}
            </div>
          </section>
        )}
      </div>
    </aside>
  );
}

function TeamSelectionDock({
  pending,
  selecting,
  agents,
  members,
  onConfirm,
  onCancel,
}: {
  pending: PendingTeamSelection | null;
  selecting: boolean;
  agents: Agent[];
  members: TeamMember[];
  onConfirm: (workerIds: string[]) => void;
  onCancel: () => void;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const workerMembers = members.filter((member) => member.role === 'worker');
  const allWorkerIds = workerMembers.map((member) => member.agentId);
  const selected = new Set(pending?.selectedAgentIds || []);
  const selectedNames = workerMembers
    .filter((member) => selected.has(member.agentId))
    .map((member) => agents.find((agent) => agent.id === member.agentId)?.name || member.name);

  return (
    <div className="mx-auto mb-3 max-w-4xl rounded-xl bg-[var(--color-bg-secondary)] p-3 shadow-md transition-all duration-300 ease-out">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
            {selecting && <Loader2 size={14} className="animate-spin text-[var(--color-accent)]" />}
            <span>{selecting ? '正在准备 Team 协作' : '确认 Team 协作'}</span>
          </div>
          <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {selecting
              ? '系统正在根据当前任务准备相关 Worker。'
              : selectedNames.length > 0
                ? `建议使用 ${selectedNames.length} 个 Worker，可直接确认或调整为全部/仅 Leader。`
                : '建议仅由 Leader 处理，不启动 Worker。'}
          </div>
        </div>
        {!selecting && (
          <div className="flex shrink-0 items-center gap-1">
            <Button size="small" type="text" onClick={() => setShowDetails((value) => !value)}>
              {showDetails ? '收起' : '查看建议'}
            </Button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md px-2 py-1 text-xs text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)]"
            >
              取消
            </button>
          </div>
        )}
      </div>
      {!selecting && pending && showDetails && (
        <div className="mt-2 rounded-md bg-[var(--color-bg-primary)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
          <div className="text-[var(--color-text-tertiary)]">{pending.reason || '模型已给出 Worker 建议。'}</div>
          <div className="mt-1">
            {selectedNames.length > 0 ? selectedNames.join('、') : '仅 Leader'}
          </div>
        </div>
      )}
      {!selecting && pending && (
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <Button onClick={() => onConfirm([])}>
            仅 Leader
          </Button>
          <Button onClick={() => onConfirm(allWorkerIds)}>
            全部 Worker
          </Button>
          <Button type="primary" onClick={() => onConfirm(pending.selectedAgentIds)}>
            确认协作
          </Button>
        </div>
      )}
    </div>
  );
}

function ProcessDetailsBlock({
  defaultExpanded,
  hasFinalAnswer,
  processStartedAt,
  processCompletedAt,
  reactTraceContent,
  thinkingContent,
  teamActivities,
  skillCalls,
  memorySummaries,
  modelCaches,
  toolCalls,
  subagentCalls,
  selectedTeamActivityId,
  onSelectTeamActivity,
  onOpenSubagent,
}: {
  defaultExpanded: boolean;
  hasFinalAnswer: boolean;
  processStartedAt?: string;
  processCompletedAt?: string;
  reactTraceContent?: string;
  thinkingContent: string;
  teamActivities: TeamActivityInfo[];
  skillCalls: SkillCallInfo[];
  memorySummaries: MemorySummaryInfo[];
  modelCaches: ModelCacheInfo[];
  toolCalls: ToolCallInfo[];
  subagentCalls: SubagentCallInfo[];
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  onOpenSubagent: (toolCallId: string) => void;
}) {
  const hasThinking = Boolean(thinkingContent.trim());
  const hasReactTrace = Boolean((reactTraceContent || '').trim());
  const regularToolCalls = useMemo(() => toolCalls.filter((toolCall) => !isSubagentToolName(toolCall.name)), [toolCalls]);
  const [expanded, setExpanded] = useState(defaultExpanded && !hasFinalAnswer);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const hadFinalAnswerRef = useRef(hasFinalAnswer);
  const processDuration = formatProcessDuration(processStartedAt, processCompletedAt, nowMs);
  const memoryFactCount = memorySummaries.reduce((total, summary) => total + summary.facts.length, 0);
  const detailCount = [
    hasReactTrace ? 1 : 0,
    thinkingContent ? 1 : 0,
    teamActivities.length,
    skillCalls.length,
    subagentCalls.length,
    memoryFactCount,
    modelCaches.length,
    regularToolCalls.length,
  ].reduce((total, count) => total + count, 0);

  useEffect(() => {
    if (hasThinking && !hasFinalAnswer) {
      setExpanded(true);
      hadFinalAnswerRef.current = hasFinalAnswer;
      return;
    }
    if (!hadFinalAnswerRef.current && hasFinalAnswer) {
      setExpanded(false);
    }
    hadFinalAnswerRef.current = hasFinalAnswer;
  }, [hasFinalAnswer, hasThinking]);

  useEffect(() => {
    if (processCompletedAt || !processStartedAt) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [processCompletedAt, processStartedAt]);

  if (detailCount === 0) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition hover:bg-[var(--color-bg-hover)]"
      >
        <div className="flex min-w-0 items-center gap-2">
          <GitBranch size={15} className="shrink-0 text-[var(--color-text-tertiary)]" />
          <span className="truncate text-sm font-medium text-[var(--color-text-primary)]">已处理</span>
          {processDuration && (
            <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
              耗时 {processDuration}
            </span>
          )}
          <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
            {detailCount} 项
          </span>
        </div>
        {expanded ? (
          <ChevronDown size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
        )}
      </button>

      {expanded && (
        <div className="max-h-[400px] space-y-2 overflow-y-auto border-t border-[var(--color-border)] p-3 pr-2">
          {modelCaches.length > 0 && (
            <ModelCacheBlock caches={modelCaches} />
          )}

          {thinkingContent && (
            <ThinkingBlock
              content={thinkingContent}
              defaultExpanded={defaultExpanded && !hasFinalAnswer}
            />
          )}

          {hasReactTrace && (
            <ReactTraceBlock content={reactTraceContent || ''} />
          )}

          {teamActivities.length > 0 && (
            <TeamActivityPanel
              activities={teamActivities}
              selectedActivityId={selectedTeamActivityId}
              onSelectActivity={onSelectTeamActivity}
            />
          )}

          {skillCalls.length > 0 && (
            <div className="space-y-1">
              {skillCalls.map((skill) => (
                <SkillCallBlock key={skill.id} skillCall={skill} />
              ))}
            </div>
          )}

          {subagentCalls.length > 0 && (
            <SubagentCallsBlock
              calls={subagentCalls}
              onOpenSubagent={onOpenSubagent}
            />
          )}

          {memorySummaries.length > 0 && (
            <MemorySummaryBlock summaries={memorySummaries} />
          )}

          {regularToolCalls.length > 0 && (
            <ToolCallsScrollBlock toolCalls={regularToolCalls} />
          )}
        </div>
      )}
    </div>
  );
}

function PlanOpenButton({ plan, onOpen }: { plan: PlanRunInfo; onOpen: (planId: string) => void }) {
  const completedSteps = plan.steps.filter((step) => step.status === 'completed').length;

  return (
    <button
      type="button"
      onClick={() => onOpen(plan.id)}
      className="flex w-fit max-w-full items-center gap-2 rounded-md bg-[var(--color-bg-secondary)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)] shadow-sm transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
    >
      <ListChecks size={13} className="shrink-0" />
      <span className="truncate">查看计划</span>
      <Tag size="small" color={plan.status === 'completed' ? 'green' : plan.status === 'failed' ? 'red' : 'blue'}>
        {completedSteps}/{plan.steps.length}
      </Tag>
    </button>
  );
}

function ToolCallsScrollBlock({ toolCalls }: { toolCalls: ToolCallInfo[] }) {
  const visibleLimit = 6;
  const overflowCount = Math.max(0, toolCalls.length - visibleLimit);

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Wrench size={14} color="var(--color-accent)" />
          <span>工具调用</span>
        </div>
        <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
          {toolCalls.length} 次{overflowCount > 0 ? ` · 滚动查看其余 ${overflowCount} 次` : ''}
        </span>
      </div>
      <div className="max-h-[260px] space-y-1 overflow-y-auto px-3 pb-3 pr-2">
        {toolCalls.map((toolCall) => (
          <ToolCallBlock key={toolCall.id} toolCall={toolCall} />
        ))}
      </div>
    </div>
  );
}

function SubagentCallsBlock({
  calls,
  onOpenSubagent,
}: {
  calls: ToolCallInfo[];
  onOpenSubagent: (toolCallId: string) => void;
}) {
  const overflowCount = Math.max(0, calls.length - 4);

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Bot size={14} color="var(--color-accent)" />
          <span>子 Agent 调用</span>
        </div>
        <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
          {calls.length} 次{overflowCount > 0 ? ` · 滚动查看其余 ${overflowCount} 次` : ''}
        </span>
      </div>
      <div className="max-h-[240px] space-y-2 overflow-y-auto px-3 pb-3 pr-2">
        {calls.map((toolCall) => (
          <SubagentCallCard
            key={toolCall.id}
            toolCall={toolCall}
            onOpen={() => onOpenSubagent(toolCall.id)}
          />
        ))}
      </div>
    </div>
  );
}

function SubagentCallCard({ toolCall, onOpen }: { toolCall: ToolCallInfo; onOpen: () => void }) {
  const summary = getSubagentCallSummary(toolCall);
  const isRunning = toolCall.status === 'calling' || toolCall.status === 'running';
  const isFailed = toolCall.status === 'failed' || summary.status === 'failed';

  return (
    <button
      type="button"
      onClick={onOpen}
      className={[
        'flex w-full items-start gap-3 rounded-lg border px-3 py-2 text-left transition hover:bg-[var(--color-bg-hover)]',
        isFailed
          ? 'border-[var(--color-error)]'
          : isRunning
            ? 'border-[var(--color-accent)]'
            : 'border-[var(--color-border)]',
      ].join(' ')}
    >
      <div className={[
        'mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg',
        isFailed
          ? 'bg-[var(--color-error-bg)] text-[var(--color-error)]'
          : 'bg-[var(--color-bg-primary)] text-[var(--color-accent)]',
      ].join(' ')}>
        {isRunning ? <Loader2 size={14} className="animate-spin" /> : <Bot size={14} />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium text-[var(--color-text-primary)]">
            {summary.agentName}
          </span>
          <Tag size="small" color={isFailed ? 'red' : isRunning ? 'blue' : 'green'}>
            {isFailed ? '失败' : isRunning ? '运行中' : '完成'}
          </Tag>
        </div>
        <div className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
          {summary.task || summary.outputPreview || '查看子 Agent 调用详情'}
        </div>
      </div>
      <ChevronRight size={14} className="mt-1 shrink-0 text-[var(--color-text-tertiary)]" />
    </button>
  );
}

function PlanRuntimeSidebar({ plan, onClose }: { plan: PlanRunInfo; onClose: () => void }) {
  const completedSteps = plan.steps.filter((step) => step.status === 'completed').length;
  const activeStep = plan.steps.find((step) => step.id === plan.currentStepId || step.status === 'active');

  return (
    <aside className="flex h-full min-h-0 flex-col bg-[var(--color-bg-secondary)] shadow-inner">
      <div className="flex items-start gap-3 px-4 py-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-primary)] text-[var(--color-text-tertiary)]">
          <ListChecks size={16} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-base font-medium text-[var(--color-text-primary)]">
            Plan Runtime
          </div>
          <div className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
            {completedSteps}/{plan.steps.length} 步 · {getPlanStatusText(plan.status)}
          </div>
        </div>
        <button
          type="button"
          className="icon-button h-8 w-8 rounded-md text-[var(--color-text-secondary)] transition-all duration-300 ease-out hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
          onClick={onClose}
          aria-label="关闭计划"
          title="关闭计划"
        >
          <X size={14} />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 pb-4">
        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">目标</div>
          <div className="whitespace-pre-wrap break-words text-sm text-[var(--color-text-primary)]">
            {plan.goal}
          </div>
          {activeStep && (
            <div className="mt-3 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
              当前步骤：{activeStep.title}
            </div>
          )}
        </section>

        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">步骤</div>
          <div className="space-y-2">
            {plan.steps.map((step) => (
              <div key={step.id} className="flex items-start gap-2 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
                <span className={[
                  'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border text-[11px]',
                  step.status === 'completed'
                    ? 'border-[var(--color-success)] bg-[var(--color-success)] text-white'
                    : step.status === 'active'
                      ? 'border-[var(--color-accent)] text-[var(--color-accent)]'
                      : 'border-[var(--color-border)] text-[var(--color-text-tertiary)]',
                ].join(' ')}>
                  {step.status === 'completed' ? '✓' : step.stepIndex + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-[var(--color-text-primary)]">{step.title}</div>
                  {step.description && (
                    <div className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">{step.description}</div>
                  )}
                  {step.evidence && (
                    <div className="mt-2 whitespace-pre-wrap break-words text-xs text-[var(--color-text-secondary)]">{step.evidence}</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">plan.md</div>
          <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-[var(--color-bg-secondary)] p-3 text-xs leading-5 text-[var(--color-text-secondary)]">
            {plan.markdown}
          </pre>
        </section>
      </div>
    </aside>
  );
}

function SubagentCallSidebar({ toolCall, onClose }: { toolCall: ToolCallInfo; onClose: () => void }) {
  const summary = getSubagentCallSummary(toolCall);
  const isRunning = toolCall.status === 'calling' || toolCall.status === 'running';
  const isFailed = toolCall.status === 'failed' || summary.status === 'failed';
  const statusText = isFailed ? '失败' : isRunning ? '运行中' : '已完成';

  return (
    <aside className="flex h-full min-h-0 flex-col bg-[var(--color-bg-secondary)] shadow-inner">
      <div className="flex items-start gap-3 px-4 py-4">
        <div className={[
          'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
          isFailed
            ? 'bg-[var(--color-error-bg)] text-[var(--color-error)]'
            : 'bg-[var(--color-bg-primary)] text-[var(--color-accent)]',
        ].join(' ')}>
          {isRunning ? <Loader2 size={16} className="animate-spin" /> : <Bot size={16} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-base font-medium text-[var(--color-text-primary)]">
            {summary.agentName}
          </div>
          <div className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
            子 Agent · {statusText}{summary.durationText ? ` · ${summary.durationText}` : ''}
          </div>
        </div>
        <button
          type="button"
          className="icon-button h-8 w-8 rounded-md text-[var(--color-text-secondary)] transition-all duration-300 ease-out hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
          onClick={onClose}
          aria-label="关闭子 Agent 详情"
          title="关闭子 Agent 详情"
        >
          <X size={14} />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 pb-4">
        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">委派任务</div>
          <div className="whitespace-pre-wrap break-words text-sm leading-6 text-[var(--color-text-primary)]">
            {summary.task || '未记录任务内容'}
          </div>
          {summary.context && (
            <div className="mt-3 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">上下文</div>
              <div className="max-h-[160px] overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-[var(--color-text-secondary)]">
                {summary.context}
              </div>
            </div>
          )}
          {summary.expectedOutput && (
            <div className="mt-3 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">期望输出</div>
              <div className="whitespace-pre-wrap break-words text-xs leading-5 text-[var(--color-text-secondary)]">
                {summary.expectedOutput}
              </div>
            </div>
          )}
        </section>

        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="text-xs text-[var(--color-text-tertiary)]">执行结果</div>
            <Tag size="small" color={isFailed ? 'red' : isRunning ? 'blue' : 'green'}>
              {statusText}
            </Tag>
          </div>
          {summary.error ? (
            <div className="whitespace-pre-wrap break-words rounded-md bg-[var(--color-error-bg)] px-3 py-2 text-sm text-[var(--color-error)]">
              {summary.error}
            </div>
          ) : summary.output ? (
            <div className="max-h-[460px] overflow-auto rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
              <MarkdownContent content={summary.output} />
            </div>
          ) : (
            <div className="text-sm text-[var(--color-text-tertiary)]">
              {isRunning ? '子 Agent 正在执行，结果会在完成后显示。' : '暂无输出。'}
            </div>
          )}
        </section>

        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">原始调用</div>
          <pre className="max-h-[240px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-[var(--color-bg-secondary)] p-3 text-xs leading-5 text-[var(--color-text-secondary)]">
            {JSON.stringify({
              id: toolCall.id,
              name: toolCall.name,
              status: toolCall.status,
              args: parseJsonObject(toolCall.args) || toolCall.args,
              result: parseJsonObject(toolCall.result) || toolCall.result,
            }, null, 2)}
          </pre>
        </section>
      </div>
    </aside>
  );
}

function getPlanStatusText(status: PlanRunInfo['status']): string {
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'cancelled') return '已取消';
  if (status === 'blocked') return '阻塞';
  if (status === 'waiting_approval') return '等待审批';
  if (status === 'replanning') return '重规划';
  return '运行中';
}

function MemorySummaryBlock({ summaries }: { summaries: MemorySummaryInfo[] }) {
  const factCount = summaries.reduce((total, summary) => total + summary.facts.length, 0);

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <BookOpenCheck size={14} color="var(--color-success)" />
          <span>候选记忆生成</span>
        </div>
        <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
          {factCount} 条
        </span>
      </div>
      <div className="space-y-2 px-3 pb-3">
        {summaries.map((summary) => (
          <div key={`${summary.agentId}-${summary.facts.map((fact) => fact.id).join('-')}`} className="rounded-md bg-[var(--color-bg-primary)] p-3">
            <div className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">
              {summary.agentName}
            </div>
            <div className="space-y-2">
              {summary.facts.map((fact) => (
                <div key={fact.id}>
                  <div className="text-sm font-medium text-[var(--color-text-primary)]">
                    {fact.title}
                  </div>
                  <div className="mt-1 line-clamp-3 text-xs text-[var(--color-text-tertiary)]">
                    {fact.content}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ModelCacheBlock({ caches }: { caches: ModelCacheInfo[] }) {
  const [expanded, setExpanded] = useState(false);
  const createdTokens = caches.reduce((total, cache) => total + cache.cacheCreationInputTokens, 0);
  const hitTokens = caches.reduce((total, cache) => total + cache.cacheInputTokens, 0);

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition hover:bg-[var(--color-bg-hover)]"
      >
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Clock3 size={14} color="var(--color-accent)" />
          <span>服务端 Prompt Cache</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-xs text-[var(--color-text-tertiary)]">
            创建 {createdTokens} · 命中 {hitTokens}
          </span>
          {expanded ? (
            <ChevronDown size={14} className="text-[var(--color-text-tertiary)]" />
          ) : (
            <ChevronRight size={14} className="text-[var(--color-text-tertiary)]" />
          )}
        </div>
      </button>
      {expanded && (
        <div className="space-y-2 px-3 pb-3">
          {caches.map((cache) => (
            <div
              key={cache.id}
              className="rounded-md bg-[var(--color-bg-primary)] px-3 py-2"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm text-[var(--color-text-primary)]">
                    {cache.modelName || 'model'}
                  </div>
                  <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                    输入 {cache.inputTokens} · 输出 {cache.outputTokens}
                  </div>
                  {(cache.cacheCreationInputTokens > 0 || cache.cacheInputTokens > 0) && (
                    <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                      命中率 {Math.round(cache.cacheHitRate * 100)}% · 约节省输入 {cache.estimatedSavedInputTokens} token
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 flex-wrap justify-end gap-1">
                  <Tag size="small" color={cache.cacheCreationInputTokens > 0 ? 'blue' : 'transparent'}>
                    创建 {cache.cacheCreationInputTokens}
                  </Tag>
                  <Tag size="small" color={cache.cacheInputTokens > 0 ? 'success' : 'transparent'}>
                    命中 {cache.cacheInputTokens}
                  </Tag>
                </div>
              </div>
              {cache.cacheCreationInputTokens === 0 && cache.cacheInputTokens === 0 && (
                <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                  本次模型响应未返回服务端缓存用量。
                </div>
              )}
            </div>
          ))}
          <div className="text-xs text-[var(--color-text-tertiary)]">
            共 {caches.length} 次模型调用返回缓存统计。
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * ReactTraceBlock — ReAct 过程记录
 */
function ReactTraceBlock({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);

  if (!content.trim()) return null;

  return (
    <div
      style={{
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--color-border)',
        overflow: 'hidden',
        fontSize: 'var(--text-sm)',
      }}
    >
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          width: '100%',
          minWidth: 0,
          padding: '6px 10px',
          border: 'none',
          backgroundColor: 'var(--color-bg-tertiary)',
          color: 'var(--color-text-secondary)',
          cursor: 'pointer',
          fontSize: 'var(--text-xs)',
        }}
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        过程记录
      </button>
      {expanded && (
        <div
          style={{
            padding: '8px 10px',
            backgroundColor: 'var(--color-bg-secondary)',
            color: 'var(--color-text-secondary)',
            maxHeight: '220px',
            overflow: 'auto',
            fontSize: 'var(--text-xs)',
            lineHeight: '1.7',
            whiteSpace: 'pre-wrap',
          }}
        >
          {content}
        </div>
      )}
    </div>
  );
}

/**
 * ThinkingBlock — 可折叠的思考链展示
 */
function ThinkingBlock({ content, defaultExpanded = false }: { content: string; defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  useEffect(() => {
    if (defaultExpanded && content.trim()) {
      setExpanded(true);
    }
  }, [content, defaultExpanded]);

  return (
    <div
      style={{
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--color-border)',
        overflow: 'hidden',
        fontSize: 'var(--text-sm)',
      }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          width: '100%',
          minWidth: 0,
          padding: '6px 10px',
          border: 'none',
          backgroundColor: 'var(--color-bg-tertiary)',
          color: 'var(--color-text-secondary)',
          cursor: 'pointer',
          fontSize: 'var(--text-xs)',
        }}
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        深度思考
      </button>
      {expanded && (
        <div
          style={{
            padding: '8px 10px',
            backgroundColor: 'var(--color-bg-secondary)',
            color: 'var(--color-text-secondary)',
            maxHeight: '200px',
            overflow: 'auto',
            fontSize: 'var(--text-xs)',
            lineHeight: '1.6',
          }}
        >
          <MarkdownContent content={content} />
        </div>
      )}
    </div>
  );
}

/**
 * SkillCallBlock — Skill 注入/应用反馈
 */
function SkillCallBlock({ skillCall }: { skillCall: SkillCallInfo }) {
  const [expanded, setExpanded] = useState(false);
  const statusText = skillCall.status === 'running'
    ? '使用中'
    : skillCall.status === 'failed'
      ? '失败'
      : '已使用';
  const statusColor = skillCall.status === 'running'
    ? 'blue'
    : skillCall.status === 'failed'
      ? 'danger'
      : 'success';

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition hover:bg-[var(--color-bg-hover)]"
      >
        <div className="flex min-w-0 items-center gap-2">
          <BookOpenCheck size={14} color="var(--color-success)" />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {skillCall.name}
            </div>
            <div className="truncate text-xs text-[var(--color-text-tertiary)]">
              {skillCall.trigger || skillCall.description || '已注入 Skill 操作手册'}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Tag size="small" color={statusColor}>
            {statusText}
          </Tag>
          {expanded ? (
            <ChevronDown size={14} className="text-[var(--color-text-tertiary)]" />
          ) : (
            <ChevronRight size={14} className="text-[var(--color-text-tertiary)]" />
          )}
        </div>
      </button>
      {expanded && (
        <div className="space-y-2 px-3 pb-3">
          {skillCall.content && (
            <div className="max-h-72 overflow-auto rounded-md bg-[var(--color-bg-primary)] p-3 text-xs leading-6 text-[var(--color-text-secondary)]">
              <MarkdownContent content={skillCall.content} />
            </div>
          )}
          {skillCall.resources.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {skillCall.resources.slice(0, 8).map((resource) => (
                <Tag key={resource} size="small" color="transparent">
                  {resource}
                </Tag>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
function HitlApprovalBlock({
  request,
  onResolve,
}: {
  request: HitlRequestInfo;
  onResolve: (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState<'approve' | 'reject' | null>(null);
  const isExternal = request.type === 'REQUIRE_EXTERNAL_EXECUTION';
  const isPlanApproval = request.type === 'PLAN_APPROVAL';
  const askUserToolCall = request.toolCalls.find((toolCall) => isAskUserToolName(toolCall.rawName || toolCall.name));
  const askUserRequest = askUserToolCall ? buildClarificationRequestFromAskUser(askUserToolCall) : null;

  const handleResolve = async (action: 'approve' | 'reject') => {
    setSubmitting(action);
    try {
      await onResolve(request.interactionId, action);
    } finally {
      setSubmitting(null);
    }
  };

  if (askUserToolCall && askUserRequest) {
    return (
      <ClarificationBlock
        request={askUserRequest}
        onSubmit={async (content) => {
          setSubmitting('approve');
          try {
            await onResolve(request.interactionId, 'approve', {
              [askUserToolCall.id]: content,
            });
          } finally {
            setSubmitting(null);
          }
        }}
        onCancel={async () => {
          setSubmitting('reject');
          try {
            await onResolve(request.interactionId, 'reject');
          } finally {
            setSubmitting(null);
          }
        }}
        submittingOverride={submitting === 'approve'}
        cancelSubmittingOverride={submitting === 'reject'}
      />
    );
  }

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          {isPlanApproval ? (
            <ListChecks size={14} color="var(--color-accent)" />
          ) : (
            <AlertCircle size={14} color="var(--color-warning)" />
          )}
          <span>{isPlanApproval ? '等待计划确认' : isExternal ? '等待外部执行结果' : '等待人工确认'}</span>
        </div>
        <Tag size="small" color="transparent">
          {request.scope === 'worker' ? 'Worker' : 'Leader'} · {request.agentName}
        </Tag>
      </div>
      <div className="space-y-2 px-3 pb-3">
        {request.toolCalls.map((toolCall) => (
          <div key={toolCall.id} className="rounded-md bg-[var(--color-bg-primary)] p-3">
            <div className="mb-2 flex items-center gap-2">
              {isPlanApproval ? (
                <ListChecks size={13} color="var(--color-accent)" />
              ) : (
                <Wrench size={13} color="var(--color-accent)" />
              )}
              <span className="text-sm font-medium text-[var(--color-text-primary)]">
                {toolCall.name}
              </span>
            </div>
            {toolCall.input && (
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--color-bg-tertiary)] p-2 text-xs text-[var(--color-text-secondary)]">
                {formatToolInput(toolCall.input)}
              </pre>
            )}
          </div>
        ))}
        <div className="flex justify-end gap-2">
          <Button
            danger
            disabled={Boolean(submitting)}
            loading={submitting === 'reject'}
            onClick={() => handleResolve('reject')}
          >
            {isPlanApproval ? '拒绝计划' : '拒绝'}
          </Button>
          <Button
            type="primary"
            disabled={Boolean(submitting)}
            loading={submitting === 'approve'}
            onClick={() => handleResolve('approve')}
          >
            {isPlanApproval ? '确认计划' : '批准'}
          </Button>
        </div>
      </div>
    </div>
  );
}

function isAskUserToolName(name: string) {
  const normalized = name.replace(/\s+/g, '').toLowerCase();
  return normalized === 'askuser' || normalized.endsWith('_askuser');
}

function buildClarificationRequestFromAskUser(toolCall: HitlRequestInfo['toolCalls'][number]): ClarificationRequestInfo {
  let raw: Record<string, unknown>;
  try {
    raw = JSON.parse(toolCall.input || '{}') as Record<string, unknown>;
  } catch {
    raw = {};
  }

  const questions = Array.isArray(raw.questions) ? raw.questions : [];
  const parsedQuestions = questions.flatMap((item, index) => {
    if (!item || typeof item !== 'object') return [];
    const question = item as Record<string, unknown>;
    const type = String(question.type || 'text');
    if (!['choice', 'multi_choice', 'text'].includes(type)) return [];
    const options = Array.isArray(question.options)
      ? question.options.map((option) => String(option)).filter(Boolean).slice(0, 5)
      : [];
    if ((type === 'choice' || type === 'multi_choice') && options.length < 2) return [];
    return [{
      id: String(question.id || `q${index + 1}`),
      type: type as ClarificationQuestionInfo['type'],
      label: String(question.label || question.question || `问题 ${index + 1}`),
      options,
      placeholder: String(question.placeholder || ''),
      required: question.required !== false,
    }];
  }).slice(0, 3);

  return {
    id: `ask-user-${toolCall.id}`,
    title: String(raw.title || '需要补充信息'),
    description: String(raw.description || 'Agent 需要这些信息后才能继续执行。'),
    createdAt: new Date().toISOString(),
    questions: parsedQuestions.length > 0
      ? parsedQuestions
      : [{
        id: 'details',
        type: 'text',
        label: '请补充信息',
        options: [],
        placeholder: '输入后 Agent 会继续当前执行',
        required: true,
      }],
  };
}

function HitlDock({
  requests,
  onResolve,
}: {
  requests: HitlRequestInfo[];
  onResolve: (interactionId: string, action: 'approve' | 'reject', externalResults?: Record<string, string>) => Promise<void>;
}) {
  if (requests.length === 0) return null;
  const onlyPlanApproval = requests.every((request) => request.type === 'PLAN_APPROVAL');

  return (
    <div className="mx-auto mb-3 max-w-4xl rounded-xl border border-[var(--color-warning)] bg-[var(--color-bg-primary)] p-3 shadow-lg">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          {onlyPlanApproval ? (
            <ListChecks size={15} color="var(--color-accent)" />
          ) : (
            <AlertCircle size={15} color="var(--color-warning)" />
          )}
          <span>{onlyPlanApproval ? '需要确认计划' : '需要人工审批'}</span>
          <Tag size="small" color="warning">
            {requests.length}
          </Tag>
        </div>
        <div className="text-xs text-[var(--color-text-tertiary)]">
          {onlyPlanApproval ? '确认后 Agent 才会开始执行计划' : '审批完成后 Agent 会继续执行'}
        </div>
      </div>
      <div className="max-h-[320px] space-y-2 overflow-y-auto pr-1">
        {requests.map((request) => (
          <HitlApprovalBlock
            key={request.interactionId}
            request={request}
            onResolve={onResolve}
          />
        ))}
      </div>
    </div>
  );
}

function ClarificationDock({
  requests,
  onSubmit,
}: {
  requests: ClarificationRequestInfo[];
  onSubmit: (content: string) => Promise<void>;
}) {
  if (requests.length === 0) return null;

  return (
    <div className="mx-auto mb-3 max-w-4xl rounded-xl border border-[var(--color-accent)] bg-[var(--color-bg-primary)] p-3 shadow-lg">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <AlertCircle size={15} color="var(--color-accent)" />
          <span>需要补充信息</span>
          <Tag size="small" color="blue">
            {requests.length}
          </Tag>
        </div>
        <div className="text-xs text-[var(--color-text-tertiary)]">
          提交后 Agent 会继续处理
        </div>
      </div>
      <div className="max-h-[360px] space-y-2 overflow-y-auto pr-1">
        {requests.map((request) => (
          <ClarificationBlock
            key={request.id}
            request={request}
            onSubmit={onSubmit}
          />
        ))}
      </div>
    </div>
  );
}

function ClarificationBlock({
  request,
  onSubmit,
  onCancel,
  submittingOverride,
  cancelSubmittingOverride,
}: {
  request: ClarificationRequestInfo;
  onSubmit: (content: string) => Promise<void>;
  onCancel?: () => Promise<void>;
  submittingOverride?: boolean;
  cancelSubmittingOverride?: boolean;
}) {
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({});
  const [submitting, setSubmitting] = useState(false);

  const setAnswer = (id: string, value: string | string[]) => {
    setAnswers((current) => ({ ...current, [id]: value }));
  };

  const toggleMulti = (id: string, option: string) => {
    const current = Array.isArray(answers[id]) ? answers[id] as string[] : [];
    setAnswer(
      id,
      current.includes(option)
        ? current.filter((item) => item !== option)
        : [...current, option],
    );
  };

  const handleSubmit = async () => {
    const missing = request.questions.some((question) => {
      if (!question.required) return false;
      const value = answers[question.id];
      return Array.isArray(value) ? value.length === 0 : !String(value || '').trim();
    });
    if (missing) return;

    const lines = request.questions.map((question) => {
      const value = answers[question.id];
      const answer = Array.isArray(value) ? value.join('、') : String(value || '').trim();
      return `- ${question.label}: ${answer || '未填写'}`;
    });
    setSubmitting(true);
    try {
      await onSubmit(`补充信息：\n${lines.join('\n')}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded-lg bg-[var(--color-bg-secondary)] p-3 shadow-sm">
      <div className="text-sm font-medium text-[var(--color-text-primary)]">
        {request.title}
      </div>
      {request.description && (
        <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
          {request.description}
        </div>
      )}
      <div className="mt-3 space-y-3">
        {request.questions.map((question) => (
          <div key={question.id}>
            <div className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">
              {question.label}
            </div>
            {question.type === 'choice' && (
              <div className="flex flex-wrap gap-2">
                {question.options.map((option) => (
                  <Button
                    key={option}
                    size="small"
                    type={answers[question.id] === option ? 'primary' : 'default'}
                    onClick={() => setAnswer(question.id, option)}
                  >
                    {option}
                  </Button>
                ))}
              </div>
            )}
            {question.type === 'multi_choice' && (
              <div className="flex flex-wrap gap-2">
                {question.options.map((option) => {
                  const selected = Array.isArray(answers[question.id]) && (answers[question.id] as string[]).includes(option);
                  return (
                    <label
                      key={option}
                      className={`flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1 text-xs transition ${
                        selected
                          ? 'border-[var(--color-accent)] bg-[var(--color-accent)] text-[var(--color-text-inverse)]'
                          : 'border-[var(--color-border)] bg-[var(--color-bg-primary)] text-[var(--color-text-secondary)]'
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="h-3 w-3"
                        checked={selected}
                        onChange={() => toggleMulti(question.id, option)}
                      />
                      {option}
                    </label>
                  );
                })}
              </div>
            )}
            {question.type === 'text' && (
              <textarea
                value={String(answers[question.id] || '')}
                onChange={(event) => setAnswer(question.id, event.target.value)}
                placeholder={question.placeholder || '请输入补充信息'}
                rows={3}
                className="w-full resize-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2 text-sm text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)]"
              />
            )}
          </div>
        ))}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        {onCancel && (
          <Button
            danger
            loading={cancelSubmittingOverride}
            disabled={submitting || submittingOverride}
            onClick={onCancel}
          >
            取消请求
          </Button>
        )}
        <Button type="primary" loading={submitting || submittingOverride} onClick={handleSubmit}>
          提交补充信息
        </Button>
      </div>
    </div>
  );
}

function formatToolInput(input: string) {
  try {
    return JSON.stringify(JSON.parse(input), null, 2);
  } catch {
    return input;
  }
}

function getTeamActivityStatusText(status: TeamActivityInfo['status']) {
  if (status === 'running') return '执行中';
  if (status === 'waiting') return '等待确认';
  if (status === 'completed') return '完成';
  if (status === 'failed') return '失败';
  return '等待';
}

/**
 * ToolCallBlock — 工具调用展示组件
 *
 * 显示工具名称、参数和执行结果。
 * 可展开/折叠查看详情。
 */
type GeneratedFileInfo = {
  path: string;
  name: string;
  extension: string;
};

function getGeneratedFileInfo(toolCall: ToolCallInfo): GeneratedFileInfo | null {
  const toolName = normalizeToolDisplayName(toolCall.name).toLowerCase();
  if ((toolName !== 'write' && toolName !== 'edit') || toolCall.status !== 'completed') {
    return null;
  }
  const args = parseToolCallArgs(toolCall.args);
  const filePath = String(args.file_path || args.path || '').trim();
  if (!filePath) {
    return null;
  }
  const name = filePath.split(/[\\/]/).filter(Boolean).pop() || 'generated-file';
  const extension = name.includes('.') ? name.split('.').pop()?.toUpperCase() || 'FILE' : 'FILE';
  return { path: filePath, name, extension };
}

function collectGeneratedFiles(toolCalls: ToolCallInfo[] = [], teamActivities: TeamActivityInfo[] = []): GeneratedFileInfo[] {
  const files: GeneratedFileInfo[] = [];
  const seen = new Set<string>();
  const addFile = (toolCall: ToolCallInfo) => {
    const file = getGeneratedFileInfo(toolCall);
    if (!file || seen.has(file.path)) return;
    seen.add(file.path);
    files.push(file);
  };

  toolCalls.forEach(addFile);
  teamActivities.forEach((activity) => {
    (activity.toolCalls || []).forEach(addFile);
  });
  return files;
}

function parseToolCallArgs(raw: string): Record<string, unknown> {
  const value = raw.trim();
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function parseJsonObject(raw: string): Record<string, unknown> | null {
  const value = raw.trim();
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function isSubagentToolName(name: string): boolean {
  return name.startsWith('子 Agent：') || name.startsWith('CallAgent_');
}

interface SubagentCallSummary {
  agentName: string;
  status: string;
  task: string;
  context: string;
  expectedOutput: string;
  output: string;
  outputPreview: string;
  error: string;
  durationText: string;
}

function getSubagentCallSummary(toolCall: ToolCallInfo): SubagentCallSummary {
  const args = parseJsonObject(toolCall.args) || {};
  const result = parseJsonObject(toolCall.result) || {};
  const nameFromTool = normalizeToolDisplayName(toolCall.name).replace(/^子 Agent：/, '').trim();
  const agentName = String(result.agentName || result.agent_name || nameFromTool || '子 Agent');
  const durationMs = Number(result.durationMs ?? result.duration_ms ?? 0);
  const output = String(result.output || '').trim();
  const error = String(result.error || '').trim();
  const task = String(args.task || '').trim();
  const context = String(args.context || '').trim();
  const expectedOutput = String(args.expected_output || args.expectedOutput || '').trim();

  return {
    agentName,
    status: String(result.status || toolCall.status || '').trim(),
    task,
    context,
    expectedOutput,
    output,
    outputPreview: output.slice(0, 160),
    error,
    durationText: Number.isFinite(durationMs) && durationMs > 0 ? formatDurationMs(durationMs) : '',
  };
}

function formatDurationMs(durationMs: number): string {
  if (durationMs < 1000) return `${durationMs}ms`;
  const seconds = durationMs / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds >= 10 ? 0 : 1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

function formatSubagentInput(raw: string): string {
  const parsed = parseJsonObject(raw);
  if (!parsed) return raw;
  const task = String(parsed.task || '').trim();
  const context = String(parsed.context || '').trim();
  const expectedOutput = String(parsed.expected_output || parsed.expectedOutput || '').trim();
  return [
    task ? `任务：${task}` : '',
    context ? `上下文：${context}` : '',
    expectedOutput ? `期望输出：${expectedOutput}` : '',
  ].filter(Boolean).join('\n\n') || raw;
}

function formatSubagentResult(raw: string): string {
  const parsed = parseJsonObject(raw);
  if (!parsed) return raw;
  const agentName = String(parsed.agentName || parsed.agent_name || '').trim();
  const status = String(parsed.status || '').trim();
  const durationMs = Number(parsed.durationMs ?? parsed.duration_ms ?? 0);
  const output = String(parsed.output || '').trim();
  const error = String(parsed.error || '').trim();
  return [
    agentName ? `子 Agent：${agentName}` : '',
    status ? `状态：${status}` : '',
    Number.isFinite(durationMs) && durationMs > 0 ? `耗时：${durationMs}ms` : '',
    output ? `输出：\n${output}` : '',
    error ? `错误：${error}` : '',
  ].filter(Boolean).join('\n\n') || raw;
}

function GeneratedFilesBlock({ files }: { files: GeneratedFileInfo[] }) {
  if (files.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] shadow-sm">
      {files.map((file, index) => (
        <div
          key={file.path}
          className={`flex min-w-0 items-center gap-3 px-4 py-3 ${index > 0 ? 'border-t border-[var(--color-border)]' : ''}`}
        >
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-primary)] text-[var(--color-text-tertiary)]">
            <FileText size={21} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]" title={file.name}>
              {file.name}
            </div>
            <div className="mt-0.5 truncate text-xs text-[var(--color-text-tertiary)]" title={file.path}>
              文档 · {file.extension}
            </div>
          </div>
          <a
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-sm font-medium text-[var(--color-text-primary)] transition hover:bg-[var(--color-bg-hover)]"
            href={`/api/tools/files/download?path=${encodeURIComponent(file.path)}`}
            download={file.name}
            title={file.path}
          >
            <Download size={15} />
            下载
          </a>
        </div>
      ))}
    </div>
  );
}

function ToolCallBlock({ toolCall }: { toolCall: ToolCallInfo }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState<'args' | 'result' | null>(null);
  const isRunning = toolCall.status === 'calling' || toolCall.status === 'running';
  const isFailed = toolCall.status === 'failed';
  const isSubagentCall = isSubagentToolName(toolCall.name);
  const displayArgs = isSubagentCall ? formatSubagentInput(toolCall.args) : toolCall.args;
  const displayResult = isSubagentCall ? formatSubagentResult(toolCall.result) : toolCall.result;

  const handleCopyToolText = useCallback(async (kind: 'args' | 'result', value: string) => {
    if (!value.trim()) return;
    await copyTextToClipboard(value);
    setCopied(kind);
    window.setTimeout(() => setCopied(null), 1200);
  }, []);

  return (
    <div
      style={{
        borderRadius: 'var(--radius-lg)',
        border: `1px solid ${isFailed ? 'var(--color-error)' : isRunning ? 'var(--color-accent)' : 'var(--color-border)'}`,
        overflow: 'hidden',
        fontSize: 'var(--text-sm)',
      }}
    >
      {/* 头部 */}
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          width: '100%',
          padding: '6px 10px',
          border: 'none',
          backgroundColor: 'transparent',
          color: 'var(--color-text-primary)',
          cursor: 'pointer',
          textAlign: 'start',
        }}
      >
        {isRunning ? (
          <Loader2 size={12} className="shrink-0 animate-spin" style={{ color: 'var(--color-accent)' }} />
        ) : isFailed ? (
          <XCircle size={12} className="shrink-0" style={{ color: 'var(--color-error)' }} />
        ) : isSubagentCall ? (
          <Bot size={12} className="shrink-0" style={{ color: 'var(--color-success)' }} />
        ) : (
          <Wrench size={12} className="shrink-0" style={{ color: 'var(--color-success)' }} />
        )}
        <span
          title={toolCall.name}
          style={{
            minWidth: 0,
            flex: 1,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontWeight: 500,
          }}
        >
          {toolCall.name}
        </span>
        <span
          style={{
            fontSize: 'var(--text-xs)',
            color: isFailed ? 'var(--color-error)' : isRunning ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
            flexShrink: 0,
          }}
        >
          {isFailed ? '失败' : isRunning ? '执行中...' : '完成'}
        </span>
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>

      {/* 展开详情 */}
      {expanded && (
        <div
          style={{
            borderTop: '1px solid var(--color-border)',
            padding: '8px 10px',
            backgroundColor: 'var(--color-bg-secondary)',
          }}
        >
          {displayArgs && (
            <div style={{ marginBottom: '6px' }}>
              <div
                style={{
                  fontSize: 'var(--text-xs)',
                  color: 'var(--color-text-tertiary)',
                  marginBottom: '2px',
                }}
              >
                <div className="flex items-center justify-between gap-2">
                  <span>参数</span>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
                    onClick={() => void handleCopyToolText('args', displayArgs)}
                  >
                    {copied === 'args' ? <Check size={12} /> : <Copy size={12} />}
                    {copied === 'args' ? '已复制' : '复制'}
                  </button>
                </div>
              </div>
              <pre
                style={{
                  fontSize: 'var(--text-xs)',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--color-text-secondary)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  margin: 0,
                  maxHeight: '120px',
                  overflow: 'auto',
                }}
              >
                {displayArgs}
              </pre>
            </div>
          )}
          {displayResult && (
            <div>
              <div
                style={{
                  fontSize: 'var(--text-xs)',
                  color: 'var(--color-text-tertiary)',
                  marginBottom: '2px',
                }}
              >
                <div className="flex items-center justify-between gap-2">
                  <span>结果</span>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
                    onClick={() => void handleCopyToolText('result', displayResult)}
                  >
                    {copied === 'result' ? <Check size={12} /> : <Copy size={12} />}
                    {copied === 'result' ? '已复制' : '复制'}
                  </button>
                </div>
              </div>
              <pre
                style={{
                  fontSize: 'var(--text-xs)',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--color-text-secondary)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  margin: 0,
                  maxHeight: '150px',
                  overflow: 'auto',
                }}
              >
                {displayResult}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
