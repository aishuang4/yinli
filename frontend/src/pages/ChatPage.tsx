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
import { Bot, Send, Square, Loader2, Wrench, ChevronDown, ChevronRight, AlertCircle, Plus, Trash2, ImagePlus, X, BookOpenCheck, Users, CheckCircle2, Clock3, Copy, Check } from 'lucide-react';
import { Button, Empty, Select, Spinner, Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../components/MarkdownContent';
import { useEventStream, type HitlRequestInfo, type MemorySummaryInfo, type SkillCallInfo, type TeamActivityInfo, type ToolCallInfo } from '../hooks/useEventStream';
import { useAgents } from '../hooks/useAgents';
import { useTeams } from '../hooks/useTeams';

/** 本地消息记录 */
interface LocalMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  /** 消息创建时间 */
  createdAt?: string;
  /** 工具调用列表（仅 assistant 消息） */
  toolCalls?: ToolCallInfo[];
  /** Skill 应用列表（仅 assistant 消息） */
  skillCalls?: SkillCallInfo[];
  /** Team 协作活动（仅 assistant 消息） */
  teamActivities?: TeamActivityInfo[];
  /** 自动记忆总结 */
  memorySummaries?: MemorySummaryInfo[];
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

interface ChatImagePayload {
  name: string;
  media_type: string;
  data: string;
}

interface ChatImageAttachment extends ChatImagePayload {
  id: string;
  previewUrl: string;
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
  images: ChatImagePayload[];
  skillCalls?: SkillCallInfo[];
  skill_calls?: SkillCallInfo[];
  teamActivities?: TeamActivityInfo[];
  team_activities?: StoredTeamActivity[];
  created_at: string;
}

type StoredTeamActivity = Partial<TeamActivityInfo> & {
  agent_id?: string;
  agent_name?: string;
  tool_calls?: ToolCallInfo[];
  created_at?: string;
};

function normalizeStoredTeamActivities(activities: StoredTeamActivity[]): TeamActivityInfo[] {
  return activities.map((activity) => ({
    id: String(activity.id || crypto.randomUUID()),
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
    toolCalls: activity.toolCalls || activity.tool_calls || [],
    createdAt: String(activity.createdAt || activity.created_at || ''),
  }));
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
  const [inputValue, setInputValue] = useState('');
  const [deepThinking, setDeepThinking] = useState(false);
  const [imageAttachments, setImageAttachments] = useState<ChatImageAttachment[]>([]);
  const [chatError, setChatError] = useState<string | null>(null);
  const [selectedTeamActivityId, setSelectedTeamActivityId] = useState<string | null>(null);
  const [renderedTeamActivity, setRenderedTeamActivity] = useState<TeamActivityInfo | null>(null);
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

  const selectedModelId = runtimeAgent?.modelConfig.primary || 'qwen-plus';
  const selectedModelSupportsImages = selectedModelId === 'qwen3.7-plus';

  const visibleTeamActivities = useMemo(() => (
    [
      ...messages.flatMap((message) => message.teamActivities || []),
      ...state.teamActivities,
    ]
  ), [messages, state.teamActivities]);

  const selectedTeamActivity = useMemo(() => (
    visibleTeamActivities.find((activity) => activity.id === selectedTeamActivityId) || null
  ), [selectedTeamActivityId, visibleTeamActivities]);

  useEffect(() => {
    if (selectedTeamActivity) {
      setRenderedTeamActivity(selectedTeamActivity);
      return undefined;
    }

    const timer = window.setTimeout(() => {
      setRenderedTeamActivity(null);
    }, 360);

    return () => window.clearTimeout(timer);
  }, [selectedTeamActivity]);

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
      images: item.images,
      skillCalls: item.skillCalls || item.skill_calls || [],
      teamActivities: normalizeStoredTeamActivities(item.teamActivities || item.team_activities || []),
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
  }, [messages, scrollToBottom, state.isStreaming, state.textContent, state.thinkingContent, state.toolCalls, state.skillCalls, state.teamActivities, state.memorySummaries]);

  const handleNewSession = useCallback(() => {
    if (state.isStreaming) return;
    setActiveSessionId(null);
    setMessages([]);
    setImageAttachments([]);
    setChatError(null);
    setSelectedTeamActivityId(null);
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
    shouldAutoScrollRef.current = true;
    setShowScrollToBottom(false);
    reset();
    await loadSessionMessages(session.id);
  }, [loadSessionMessages, reset, state.isStreaming]);

  const handleDeleteSession = useCallback(async (sessionId: string) => {
    if (state.isStreaming) return;
    const response = await fetch(`/api/chat/sessions/${sessionId}`, { method: 'DELETE' });
    if (!response.ok && response.status !== 404) throw new Error(`HTTP ${response.status}`);
    if (activeSessionId === sessionId) {
      setActiveSessionId(null);
      setMessages([]);
      setImageAttachments([]);
      setChatError(null);
      setSelectedTeamActivityId(null);
      reset();
    }
    await loadSessions();
  }, [activeSessionId, loadSessions, reset, state.isStreaming]);

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
            id: crypto.randomUUID(),
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
    const sessionId = activeSessionId || crypto.randomUUID();
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
      sessionId,
      agentId: runtimeAgent?.id,
      agentName: runtimeAgent?.name,
      teamId: targetMode === 'team' ? selectedTeam?.id : undefined,
      teamName: targetMode === 'team' ? selectedTeam?.name : undefined,
      teamMembers: targetMode === 'team'
        ? selectedTeam?.members.map((member) => ({
          agentId: member.agentId,
          name: agents.find((agent) => agent.id === member.agentId)?.name || member.name,
          role: member.role,
          description: member.description,
        }))
        : undefined,
      systemPrompt: runtimeAgent?.systemPrompt,
      model: runtimeAgent?.modelConfig.primary,
      skillIds: runtimeAgent?.skills,
      deepThinking,
      images,
    });
  }, [activeSessionId, agents, deepThinking, imageAttachments, inputValue, runtimeAgent, selectedModelId, selectedModelSupportsImages, selectedTeam, sendMessage, state.isStreaming, targetMode, teamLeaderAgent]);

  // 当流结束时，将 assistant 回复加入消息历史
  useEffect(() => {
    if (
      !state.isStreaming &&
      state.hitlRequests.length === 0 &&
      (state.textContent || state.error) &&
      messages.length > 0 &&
      messages[messages.length - 1].role === 'user'
    ) {
      const assistantMessage: LocalMessage = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: state.textContent,
        createdAt: new Date().toISOString(),
        skillCalls: state.skillCalls.length > 0 ? state.skillCalls : undefined,
        toolCalls: state.toolCalls.length > 0 ? state.toolCalls : undefined,
        teamActivities: state.teamActivities.length > 0 ? state.teamActivities : undefined,
        memorySummaries: state.memorySummaries.length > 0 ? state.memorySummaries : undefined,
        thinking: state.thinkingContent || undefined,
        tokenUsage: state.tokenUsage,
        modelName: state.modelName,
        error: state.error || undefined,
      };
      setMessages((prev) => [...prev, assistantMessage]);
      reset();
      loadSessions();
    }
  }, [loadSessions, messages, reset, state.error, state.hitlRequests.length, state.isStreaming, state.memorySummaries, state.modelName, state.skillCalls, state.teamActivities, state.textContent, state.thinkingContent, state.tokenUsage, state.toolCalls]);

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
    <div className="grid h-full min-h-0 grid-cols-[280px_1fr] gap-4 bg-[var(--color-bg-secondary)] p-4">
      <aside className="flex min-h-0 flex-col overflow-hidden rounded-xl bg-[var(--color-bg-primary)] shadow-sm">
        <div className="p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-medium text-[var(--color-text-primary)]">
              历史对话
            </h2>
            <Button size="small" type="primary" onClick={handleNewSession} disabled={state.isStreaming}>
              <Plus size={14} />
            </Button>
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
                  className={`group w-full rounded-lg px-3 py-2 text-left shadow-sm transition-all duration-300 ease-out ${
                    activeSessionId === session.id
                      ? 'bg-[var(--color-bg-secondary)] ring-1 ring-[var(--color-accent)]'
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
        className="grid min-h-0 overflow-hidden rounded-xl bg-[var(--color-bg-primary)] shadow-sm"
        style={{
          gridTemplateColumns: selectedTeamActivity
            ? 'minmax(0, 1fr) 360px'
            : 'minmax(0, 1fr) 0px',
          transition: 'grid-template-columns 360ms var(--ease-out)',
        }}
      >
        <div className="grid min-h-0 grid-rows-[auto_1fr_auto] overflow-hidden">
          <header className="flex items-center gap-4 px-5 py-4">
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
                  ? `${state.modelName || runtimeAgent?.modelConfig.primary || 'qwen-plus'} · 正在思考...`
                  : targetMode === 'team'
                    ? `${teamLeaderAgent?.name || 'Leader'} · ${selectedTeam?.members.length || 0} 个成员`
                    : runtimeAgent?.description || 'AgentScope 2.0 · 百炼 DashScope'}
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
                {messages.length === 0 && !state.isStreaming ? (
                  <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center text-[var(--color-text-tertiary)]">
                    <Bot size={48} strokeWidth={1} color="var(--color-text-tertiary)" />
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
                        onSelectTeamActivity={setSelectedTeamActivityId}
                      />
                    ))}

                    {(state.isStreaming || state.hitlRequests.length > 0) && (
                      <StreamingBubble
                        textContent={state.textContent}
                        thinkingContent={state.thinkingContent}
                        skillCalls={state.skillCalls}
                        teamActivities={state.teamActivities}
                        memorySummaries={state.memorySummaries}
                        hitlRequests={state.hitlRequests}
                        onResolveHitl={resumeInteraction}
                        selectedTeamActivityId={selectedTeamActivityId}
                        onSelectTeamActivity={setSelectedTeamActivityId}
                        toolCalls={state.toolCalls}
                        modelName={state.modelName}
                      />
                    )}

                    <div ref={messagesEndRef} />
                  </>
                )}
              </div>
            </div>
            <Button
              type="text"
              className={`absolute bottom-4 left-1/2 h-9 w-9 -translate-x-1/2 rounded-full border border-[var(--color-border)] bg-[var(--color-bg-primary)]/70 p-0 text-[var(--color-text-secondary)] shadow-sm backdrop-blur transition-all duration-300 ease-out hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)] ${
                showScrollToBottom
                  ? 'pointer-events-auto opacity-100'
                  : 'pointer-events-none translate-y-2 opacity-0'
              }`}
              onClick={() => scrollToBottom('smooth')}
              aria-label="滚动到底部"
              aria-hidden={!showScrollToBottom}
            >
              <ChevronDown size={16} />
            </Button>
          </div>

          <div className="px-5 pb-5 pt-2">
          {chatError && (
            <div className="mb-2 flex items-center gap-2 rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] px-3 py-2 text-sm text-[var(--color-error)]">
              <AlertCircle size={14} />
              {chatError}
            </div>
          )}

          {state.hitlRequests.length > 0 && (
            <HitlDock
              requests={state.hitlRequests}
              onResolve={resumeInteraction}
            />
          )}

          <div className="mx-auto max-w-4xl rounded-xl bg-[var(--color-bg-secondary)] shadow-md transition-shadow focus-within:shadow-lg">
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
              rows={3}
              disabled={state.isStreaming}
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
                  disabled={state.isStreaming}
                  onClick={() => setTargetMode('agent')}
                >
                  单 Agent
                </Button>
                <Button
                  size="small"
                  type={targetMode === 'team' ? 'primary' : 'text'}
                  disabled={state.isStreaming}
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
                  disabled={state.isStreaming}
                  style={{ width: 260 }}
                />
              ) : (
                <Select
                  value={selectedTeamId}
                  onChange={(value) => setSelectedTeamId(value)}
                  options={teamOptions}
                  loading={teamsLoading}
                  disabled={state.isStreaming}
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
                <Button
                  onClick={() => imageInputRef.current?.click()}
                  disabled={state.isStreaming || imageAttachments.length >= 4}
                >
                  <ImagePlus size={16} />
                </Button>
                {state.isStreaming ? (
                  <Button danger onClick={abort}>
                    <Square size={16} />
                    停止
                  </Button>
                ) : (
                  <Button type="primary" onClick={handleSend} disabled={!inputValue.trim() && imageAttachments.length === 0}>
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
          {renderedTeamActivity && (
            <TeamActivitySidebar
              activity={renderedTeamActivity}
              onClose={() => setSelectedTeamActivityId(null)}
            />
          )}
        </div>
      </section>
    </div>
  );
}

/**
 * MessageBubble — 历史消息气泡（已完成的消息）
 */
function MessageBubble({
  message,
  selectedTeamActivityId,
  onSelectTeamActivity,
}: {
  message: LocalMessage;
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
}) {
  const isUser = message.role === 'user';

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: isUser ? 'row-reverse' : 'row',
        alignItems: 'flex-start',
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

        {!isUser && message.thinking && <ThinkingBlock content={message.thinking} />}

        {!isUser && message.teamActivities && message.teamActivities.length > 0 && (
          <TeamActivityPanel
            activities={message.teamActivities}
            selectedActivityId={selectedTeamActivityId}
            onSelectActivity={onSelectTeamActivity}
          />
        )}

        {!isUser && message.skillCalls && message.skillCalls.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {message.skillCalls.map((skill) => (
              <SkillCallBlock key={skill.id} skillCall={skill} />
            ))}
          </div>
        )}

        {!isUser && message.memorySummaries && message.memorySummaries.length > 0 && (
          <MemorySummaryBlock summaries={message.memorySummaries} />
        )}

        {/* 工具调用记录 */}
        {!isUser && message.toolCalls && message.toolCalls.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {message.toolCalls.map((tc) => (
              <ToolCallBlock key={tc.id} toolCall={tc} />
            ))}
          </div>
        )}

        {/* 文本内容 */}
        {message.content && (
          <div
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
            {isUser ? message.content : <MarkdownContent content={message.content} />}
          </div>
        )}

        {!isUser && message.content && (
          <MessageToolbar markdown={message.content} createdAt={message.createdAt} />
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
  hitlRequests,
  onResolveHitl,
  selectedTeamActivityId,
  onSelectTeamActivity,
  toolCalls,
  modelName,
}: {
  textContent: string;
  thinkingContent: string;
  skillCalls: SkillCallInfo[];
  teamActivities: TeamActivityInfo[];
  memorySummaries: MemorySummaryInfo[];
  hitlRequests: HitlRequestInfo[];
  onResolveHitl: (interactionId: string, action: 'approve' | 'reject') => Promise<void>;
  selectedTeamActivityId: string | null;
  onSelectTeamActivity: (id: string) => void;
  toolCalls: ToolCallInfo[];
  modelName: string;
}) {
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
        {/* 思考链（可折叠） */}
        {thinkingContent && <ThinkingBlock content={thinkingContent} />}

        {teamActivities.length > 0 && (
          <TeamActivityPanel
            activities={teamActivities}
            selectedActivityId={selectedTeamActivityId}
            onSelectActivity={onSelectTeamActivity}
          />
        )}

        {skillCalls.map((skill) => (
          <SkillCallBlock key={skill.id} skillCall={skill} />
        ))}

        {memorySummaries.length > 0 && (
          <MemorySummaryBlock summaries={memorySummaries} />
        )}

        {hitlRequests.map((request) => (
          <HitlApprovalBlock
            key={request.interactionId}
            request={request}
            onResolve={onResolveHitl}
          />
        ))}

        {/* 工具调用 */}
        {toolCalls.map((tc) => (
          <ToolCallBlock key={tc.id} toolCall={tc} />
        ))}

        {/* 文本内容 */}
        {textContent && (
          <div
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
            <MarkdownContent content={textContent} streaming />
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

        {textContent && (
          <MessageToolbar markdown={textContent} createdAt={new Date().toISOString()} />
        )}

        {/* 无内容时显示加载指示器 */}
        {!textContent && toolCalls.length === 0 && skillCalls.length === 0 && teamActivities.length === 0 && memorySummaries.length === 0 && hitlRequests.length === 0 && !thinkingContent && (
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

function TeamActivityCard({
  activity,
  selected,
  onSelect,
}: {
  activity: TeamActivityInfo;
  selected: boolean;
  onSelect: () => void;
}) {
  const toolCalls = activity.toolCalls || [];
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
      className={`min-h-[132px] rounded-md bg-[var(--color-bg-primary)] p-3 text-left transition-all duration-300 ease-out ${
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
        <div className="mt-2 flex max-h-7 flex-wrap gap-1 overflow-hidden">
          {toolCalls.slice(0, 3).map((toolCall) => (
            <Tag key={toolCall.id} size="small" color="transparent">
              {toolCall.name}
            </Tag>
          ))}
          {toolCalls.length > 3 && (
            <Tag size="small" color="transparent">
              +{toolCalls.length - 3}
            </Tag>
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
  const toolCalls = activity.toolCalls || [];
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
        <Button size="small" type="text" onClick={onClose}>
          <X size={14} />
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 pb-4">
        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">当前动作</div>
          <div className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
            {isRunning && <Loader2 size={13} className="animate-spin text-[var(--color-accent)]" />}
            {isWaiting && <AlertCircle size={13} className="text-[var(--color-warning)]" />}
            <span>{activity.currentEvent || getTeamActivityStatusText(activity.status)}</span>
          </div>
        </section>

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
            {toolCalls.map((toolCall) => (
              <ToolCallBlock key={toolCall.id} toolCall={toolCall} />
            ))}
          </section>
        )}
      </div>
    </aside>
  );
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

function HitlApprovalBlock({
  request,
  onResolve,
}: {
  request: HitlRequestInfo;
  onResolve: (interactionId: string, action: 'approve' | 'reject') => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState<'approve' | 'reject' | null>(null);
  const isExternal = request.type === 'REQUIRE_EXTERNAL_EXECUTION';

  const handleResolve = async (action: 'approve' | 'reject') => {
    setSubmitting(action);
    try {
      await onResolve(request.interactionId, action);
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div className="overflow-hidden rounded-lg bg-[var(--color-bg-secondary)] shadow-sm">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <AlertCircle size={14} color="var(--color-warning)" />
          <span>{isExternal ? '等待外部执行结果' : '等待人工确认'}</span>
        </div>
        <Tag size="small" color="transparent">
          {request.scope === 'worker' ? 'Worker' : 'Leader'} · {request.agentName}
        </Tag>
      </div>
      <div className="space-y-2 px-3 pb-3">
        {request.toolCalls.map((toolCall) => (
          <div key={toolCall.id} className="rounded-md bg-[var(--color-bg-primary)] p-3">
            <div className="mb-2 flex items-center gap-2">
              <Wrench size={13} color="var(--color-accent)" />
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
            拒绝
          </Button>
          <Button
            type="primary"
            disabled={Boolean(submitting)}
            loading={submitting === 'approve'}
            onClick={() => handleResolve('approve')}
          >
            批准
          </Button>
        </div>
      </div>
    </div>
  );
}

function HitlDock({
  requests,
  onResolve,
}: {
  requests: HitlRequestInfo[];
  onResolve: (interactionId: string, action: 'approve' | 'reject') => Promise<void>;
}) {
  if (requests.length === 0) return null;

  return (
    <div className="mx-auto mb-3 max-w-4xl rounded-xl border border-[var(--color-warning)] bg-[var(--color-bg-primary)] p-3 shadow-lg">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <AlertCircle size={15} color="var(--color-warning)" />
          <span>需要人工审批</span>
          <Tag size="small" color="warning">
            {requests.length}
          </Tag>
        </div>
        <div className="text-xs text-[var(--color-text-tertiary)]">
          审批完成后 Agent 会继续执行
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
 * ThinkingBlock — 可折叠的思考链展示
 */
function ThinkingBlock({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);

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
          padding: '6px 10px',
          border: 'none',
          backgroundColor: 'var(--color-bg-tertiary)',
          color: 'var(--color-text-secondary)',
          cursor: 'pointer',
          fontSize: 'var(--text-xs)',
        }}
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        思考过程
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
      : '已完成';
  const statusColor = skillCall.status === 'failed'
    ? 'var(--color-error)'
    : skillCall.status === 'running'
      ? 'var(--color-accent)'
      : 'var(--color-success)';

  return (
    <div
      style={{
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--color-border)',
        overflow: 'hidden',
        fontSize: 'var(--text-sm)',
        backgroundColor: 'var(--color-bg-secondary)',
      }}
    >
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
        {skillCall.status === 'running' ? (
          <Loader2 size={12} className="animate-spin" style={{ color: statusColor }} />
        ) : (
          <BookOpenCheck size={12} style={{ color: statusColor }} />
        )}
        <span style={{ fontWeight: 500 }}>{skillCall.name}</span>
        <span
          style={{
            fontSize: 'var(--text-xs)',
            color: statusColor,
            marginLeft: 'auto',
          }}
        >
          {statusText}
        </span>
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {skillCall.description && (
        <div
          style={{
            borderTop: '1px solid var(--color-border)',
            padding: '6px 10px',
            color: 'var(--color-text-tertiary)',
            fontSize: 'var(--text-xs)',
          }}
        >
          {skillCall.description}
        </div>
      )}
      {expanded && (
        <div
          style={{
            borderTop: '1px solid var(--color-border)',
            padding: '8px 10px',
            backgroundColor: 'var(--color-bg-primary)',
          }}
        >
          {skillCall.trigger && (
            <div style={{ marginBottom: '8px' }}>
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">触发条件</div>
              <div className="text-xs text-[var(--color-text-secondary)]">{skillCall.trigger}</div>
            </div>
          )}
          {skillCall.resourcePath && (
            <div style={{ marginBottom: '8px' }}>
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">资源路径</div>
              <code className="block break-all rounded bg-[var(--color-bg-tertiary)] px-2 py-1 text-xs text-[var(--color-text-secondary)]">
                {skillCall.resourcePath}
              </code>
            </div>
          )}
          {skillCall.resources.length > 0 && (
            <div style={{ marginBottom: '8px' }}>
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">资源文件</div>
              <div className="flex max-h-16 flex-wrap gap-1 overflow-hidden">
                {skillCall.resources.slice(0, 8).map((resource) => (
                  <Tag key={resource} size="small" color="transparent">
                    {resource}
                  </Tag>
                ))}
                {skillCall.resources.length > 8 && (
                  <Tag size="small" color="transparent">
                    +{skillCall.resources.length - 8}
                  </Tag>
                )}
              </div>
            </div>
          )}
          {skillCall.content && (
            <div>
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">SKILL.md</div>
              <pre
                style={{
                  margin: 0,
                  maxHeight: '260px',
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  borderRadius: 'var(--radius-md)',
                  backgroundColor: 'var(--color-bg-tertiary)',
                  padding: '8px',
                  color: 'var(--color-text-secondary)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 'var(--text-xs)',
                  lineHeight: '1.6',
                }}
              >
                {skillCall.content}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * ToolCallBlock — 工具调用展示组件
 *
 * 显示工具名称、参数和执行结果。
 * 可展开/折叠查看详情。
 */
function ToolCallBlock({ toolCall }: { toolCall: ToolCallInfo }) {
  const [expanded, setExpanded] = useState(false);
  const isRunning = toolCall.status === 'calling' || toolCall.status === 'running';

  return (
    <div
      style={{
        borderRadius: 'var(--radius-lg)',
        border: `1px solid ${isRunning ? 'var(--color-accent)' : 'var(--color-border)'}`,
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
          <Loader2 size={12} className="animate-spin" style={{ color: 'var(--color-accent)' }} />
        ) : (
          <Wrench size={12} style={{ color: 'var(--color-success)' }} />
        )}
        <span style={{ fontWeight: 500 }}>{toolCall.name}</span>
        <span
          style={{
            fontSize: 'var(--text-xs)',
            color: isRunning ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
            marginLeft: 'auto',
          }}
        >
          {isRunning ? '执行中...' : '完成'}
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
          {toolCall.args && (
            <div style={{ marginBottom: '6px' }}>
              <div
                style={{
                  fontSize: 'var(--text-xs)',
                  color: 'var(--color-text-tertiary)',
                  marginBottom: '2px',
                }}
              >
                参数
              </div>
              <pre
                style={{
                  fontSize: 'var(--text-xs)',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--color-text-secondary)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  margin: 0,
                }}
              >
                {toolCall.args}
              </pre>
            </div>
          )}
          {toolCall.result && (
            <div>
              <div
                style={{
                  fontSize: 'var(--text-xs)',
                  color: 'var(--color-text-tertiary)',
                  marginBottom: '2px',
                }}
              >
                结果
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
                {toolCall.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
