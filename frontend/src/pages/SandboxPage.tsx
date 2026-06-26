import { useCallback, useEffect, useMemo, useState } from 'react';
import { Bot, Copy, FlaskConical, Loader2, Save, Send, Square, Trash2, Users, Wrench } from 'lucide-react';
import { Button, Select, Spinner, Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../components/MarkdownContent';
import { useAgents } from '../hooks/useAgents';
import { useTeams } from '../hooks/useTeams';
import { useEventStream, type TeamActivityInfo, type ToolCallInfo } from '../hooks/useEventStream';
import type { TeamMember } from '../types/team';
import { createId } from '../utils/id';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';

interface SandboxCase {
  id: string;
  name: string;
  description: string;
  payload: {
    mode: 'agent' | 'team';
    agentId: string;
    agentName: string;
    teamId: string | null;
    teamName: string | null;
    message: string;
  };
  createdAt: string;
  updatedAt: string;
}

export function SandboxPage() {
  const { agents, loading: agentsLoading } = useAgents();
  const { teams, loading: teamsLoading } = useTeams();
  const { state, sendMessage, abort, reset } = useEventStream();
  const [mode, setMode] = useState<'agent' | 'team'>('agent');
  const [agentId, setAgentId] = useState('');
  const [teamId, setTeamId] = useState('');
  const [message, setMessage] = useState('请用一句话说明你会如何完成这个任务，并在需要时调用合适工具。');
  const [cases, setCases] = useState<SandboxCase[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState('');
  const [caseName, setCaseName] = useState('');
  const [savingCase, setSavingCase] = useState(false);

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === agentId) || agents[0] || null,
    [agentId, agents]
  );
  const selectedTeam = useMemo(
    () => teams.find((team) => team.id === teamId) || teams[0] || null,
    [teamId, teams]
  );
  const runtimeAgent = mode === 'team'
    ? agents.find((agent) => agent.id === selectedTeam?.leaderAgentId) || null
    : selectedAgent;

  const loadCases = useCallback(async () => {
    const response = await fetch('/api/sandbox/cases');
    if (!response.ok) return;
    setCases(await response.json());
  }, []);

  useEffect(() => {
    void loadCases();
  }, [loadCases]);

  const saveCurrentCase = async () => {
    if (!runtimeAgent || !message.trim() || savingCase) return;
    const name = caseName.trim() || message.trim().slice(0, 40);
    setSavingCase(true);
    try {
      const response = await fetch('/api/sandbox/cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          description: '',
          payload: {
            mode,
            agentId: runtimeAgent.id,
            agentName: runtimeAgent.name,
            teamId: mode === 'team' ? selectedTeam?.id || null : null,
            teamName: mode === 'team' ? selectedTeam?.name || null : null,
            message,
          },
        }),
      });
      if (response.ok) {
        const nextCase: SandboxCase = await response.json();
        setCases((current) => [nextCase, ...current.filter((item) => item.id !== nextCase.id)]);
        setSelectedCaseId(nextCase.id);
        setCaseName('');
        notifySuccess('测试用例保存成功');
      } else {
        throw new Error(`HTTP ${response.status}`);
      }
    } catch (err) {
      notifyError(`测试用例保存失败：${getErrorMessage(err, '未知错误')}`);
    } finally {
      setSavingCase(false);
    }
  };

  const loadSelectedCase = () => {
    const item = cases.find((sandboxCase) => sandboxCase.id === selectedCaseId);
    if (!item) return;
    setMode(item.payload.mode);
    if (item.payload.mode === 'team') {
      setTeamId(item.payload.teamId || '');
    } else {
      setAgentId(item.payload.agentId);
    }
    setMessage(item.payload.message);
    setCaseName(item.name);
    reset();
  };

  const deleteSelectedCase = async () => {
    if (!selectedCaseId) return;
    try {
      const response = await fetch(`/api/sandbox/cases/${selectedCaseId}`, { method: 'DELETE' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setCases((current) => current.filter((item) => item.id !== selectedCaseId));
      setSelectedCaseId('');
      notifySuccess('测试用例删除成功');
    } catch (err) {
      notifyError(`测试用例删除失败：${getErrorMessage(err, '未知错误')}`);
    }
  };

  const runSandbox = async () => {
    if (!message.trim() || !runtimeAgent || state.isStreaming) return;
    reset();
    await sendMessage(message.trim(), {
      sessionId: createId('sandbox'),
      agentId: runtimeAgent.id,
      agentName: runtimeAgent.name,
      teamId: mode === 'team' ? selectedTeam?.id : undefined,
      teamName: mode === 'team' ? selectedTeam?.name : undefined,
      teamMembers: mode === 'team'
        ? (selectedTeam?.members || []).map((member: TeamMember) => ({
          id: member.id,
          agentId: member.agentId,
          name: member.name,
          role: member.role,
          description: member.description,
          executionMode: member.executionMode,
          dependsOn: member.dependsOn,
          handoffPrompt: member.handoffPrompt,
        }))
        : undefined,
      systemPrompt: runtimeAgent.systemPrompt,
      model: runtimeAgent.modelConfig.primary,
      skillIds: runtimeAgent.skills,
      sandbox: true,
      images: [],
    });
  };

  const loading = agentsLoading || teamsLoading;

  return (
    <div className="grid h-full min-h-0 grid-cols-[360px_1fr] gap-4 overflow-hidden p-4">
      <aside className="min-h-0 overflow-y-auto rounded-md bg-[var(--color-bg-primary)] p-4 shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
        <div className="mb-4 flex items-center gap-2">
          <FlaskConical size={18} color="var(--color-accent)" />
          <div>
            <h2 className="text-base font-medium text-[var(--color-text-primary)]">测试沙盒</h2>
            <div className="text-xs text-[var(--color-text-secondary)]">不写历史，仅用于临时验证</div>
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center py-10"><Spinner /></div>
        ) : (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">测试用例</label>
              <Select
                value={selectedCaseId || undefined}
                onChange={(value) => setSelectedCaseId(String(value))}
                options={cases.map((item) => ({ label: item.name, value: item.id }))}
                style={{ width: '100%' }}
                placeholder="选择已保存用例"
              />
              <div className="mt-2">
                <input
                  value={caseName}
                  onChange={(event) => setCaseName(event.target.value)}
                  className="w-full min-w-0 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent)]"
                  placeholder="用例名称"
                />
                <div className="mt-2 flex items-center gap-2">
                  <Button size="small" disabled={!runtimeAgent || !message.trim()} loading={savingCase} onClick={() => void saveCurrentCase()}>
                    <Save size={13} />
                    保存
                  </Button>
                  <Button size="small" disabled={!selectedCaseId} onClick={loadSelectedCase}>
                    加载用例
                  </Button>
                  <Button size="small" disabled={!selectedCaseId} onClick={() => void deleteSelectedCase()} title="删除用例">
                    <Trash2 size={13} />
                  </Button>
                </div>
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">运行模式</label>
              <div className="grid grid-cols-2 gap-2">
                <Button type={mode === 'agent' ? 'primary' : 'default'} onClick={() => setMode('agent')}>
                  <Bot size={14} />
                  单 Agent
                </Button>
                <Button type={mode === 'team' ? 'primary' : 'default'} onClick={() => setMode('team')}>
                  <Users size={14} />
                  Team
                </Button>
              </div>
            </div>

            {mode === 'agent' ? (
              <div>
                <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">Agent</label>
                <Select
                  value={selectedAgent?.id}
                  onChange={(value) => setAgentId(String(value))}
                  options={agents.map((agent) => ({ label: `${agent.name} · ${agent.modelConfig.primary}`, value: agent.id }))}
                  style={{ width: '100%' }}
                  placeholder="选择 Agent"
                />
              </div>
            ) : (
              <div>
                <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">Team</label>
                <Select
                  value={selectedTeam?.id}
                  onChange={(value) => setTeamId(String(value))}
                  options={teams.map((team) => ({ label: `${team.name} · ${team.members.length} 成员`, value: team.id }))}
                  style={{ width: '100%' }}
                  placeholder="选择 Team"
                />
              </div>
            )}

            <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
              <div className="text-sm font-medium text-[var(--color-text-primary)]">
                {runtimeAgent?.name || '未选择运行对象'}
              </div>
              <div className="mt-1 text-xs text-[var(--color-text-secondary)]">
                {runtimeAgent?.modelConfig.primary || '选择 Agent 或 Team 后开始测试'}
              </div>
              {mode === 'team' && selectedTeam && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {selectedTeam.members.slice(0, 6).map((member) => (
                    <Tag key={member.id} size="small" color={member.role === 'leader' ? 'blue' : 'mauve'}>
                      {member.name}
                    </Tag>
                  ))}
                </div>
              )}
            </div>

            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">测试输入</label>
              <textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                className="h-48 w-full resize-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2 text-sm text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent)]"
                placeholder="输入一条测试任务"
              />
            </div>

            <div className="flex gap-2">
              <Button type="primary" disabled={!runtimeAgent || !message.trim()} loading={state.isStreaming} onClick={() => void runSandbox()}>
                <Send size={14} />
                运行测试
              </Button>
              <Button disabled={!state.isStreaming} onClick={abort}>
                <Square size={14} />
                停止
              </Button>
              <Button disabled={state.isStreaming} onClick={reset}>清空</Button>
            </div>
          </div>
        )}
      </aside>

      <main className="min-h-0 overflow-y-auto rounded-md bg-[var(--color-bg-primary)] p-5 shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
        <SandboxResult state={state} />
      </main>
    </div>
  );
}

function SandboxResult({ state }: { state: ReturnType<typeof useEventStream>['state'] }) {
  if (!state.isStreaming && !state.textContent && !state.error && state.toolCalls.length === 0 && state.teamActivities.length === 0 && state.skillCalls.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-[var(--color-text-tertiary)]">
        <div className="text-center">
          <FlaskConical size={46} strokeWidth={1.2} className="mx-auto mb-3" />
          <div className="text-sm">运行一次测试后查看结果和轨迹</div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        {state.isStreaming && <Loader2 size={15} className="animate-spin text-[var(--color-accent)]" />}
        <h2 className="text-base font-medium text-[var(--color-text-primary)]">沙盒运行结果</h2>
        <Tag size="small" color="orange">Sandbox</Tag>
      </div>

      {state.error && (
        <div className="rounded-md border border-[var(--color-border)] p-3 text-sm text-[var(--color-error)]">{state.error}</div>
      )}
      {state.skillCalls.length > 0 && (
        <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
          <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">Skills</div>
          <div className="flex flex-wrap gap-1">
            {state.skillCalls.map((skill) => <Tag key={skill.id} size="small" color="green">{skill.name}</Tag>)}
          </div>
        </section>
      )}
      {state.teamActivities.length > 0 && (
        <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
          <div className="mb-3 text-sm font-medium text-[var(--color-text-primary)]">Team 轨迹</div>
          <div className="grid gap-2 md:grid-cols-2">
            {state.teamActivities.map((activity) => (
              <SandboxActivityCard key={activity.id} activity={activity} />
            ))}
          </div>
        </section>
      )}
      {state.toolCalls.length > 0 && <SandboxToolList title="Agent 工具调用" toolCalls={state.toolCalls} />}
      {state.thinkingContent && (
        <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
          <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">深度思考</div>
          <MarkdownContent content={state.thinkingContent} />
        </section>
      )}
      {state.textContent && (
        <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
          <MarkdownContent content={state.textContent} streaming={state.isStreaming} />
        </section>
      )}
    </div>
  );
}

function SandboxActivityCard({ activity }: { activity: TeamActivityInfo }) {
  const toolCalls = activity.toolCalls || [];

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">{activity.agentName}</div>
          <div className="text-xs text-[var(--color-text-tertiary)]">{activity.role}</div>
        </div>
        <Tag size="small" color={activity.status === 'failed' ? 'red' : activity.status === 'completed' ? 'green' : 'blue'}>
          {activity.status}
        </Tag>
      </div>
      <div className="line-clamp-2 text-xs text-[var(--color-text-secondary)]">{activity.detail || activity.task}</div>
      {toolCalls.length > 0 && <SandboxToolList title="工具调用" toolCalls={toolCalls} compact />}
    </div>
  );
}

function SandboxToolList({ title, toolCalls, compact = false }: { title: string; toolCalls: ToolCallInfo[]; compact?: boolean }) {
  return (
    <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
      <div className="mb-2 flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
        <Wrench size={12} />
        {title}
      </div>
      <div className={`${compact ? 'max-h-72' : 'max-h-96'} grid gap-2 overflow-y-auto pr-1`}>
        {toolCalls.map((toolCall) => (
          <div key={toolCall.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-2">
            <div className="flex min-w-0 items-center justify-between gap-2">
              <span className="min-w-0 truncate text-sm font-medium text-[var(--color-text-primary)]" title={normalizeToolDisplayName(toolCall.name)}>
                {normalizeToolDisplayName(toolCall.name)}
              </span>
              <div className="flex shrink-0 items-center gap-1">
                {(toolCall.args || toolCall.result) && (
                  <button
                    type="button"
                    className="inline-flex h-6 w-6 items-center justify-center rounded text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                    title="复制工具内容"
                    onClick={() => void copyTextToClipboard(toolCall.result || toolCall.args)}
                  >
                    <Copy size={12} />
                  </button>
                )}
                <Tag size="small" color={toolCall.status === 'failed' ? 'red' : toolCall.status === 'completed' ? 'green' : 'blue'}>{toolCall.status}</Tag>
              </div>
            </div>
            {!compact && (toolCall.args || toolCall.result) && (
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-[var(--color-text-tertiary)]">
                {toolCall.result || toolCall.args}
              </pre>
            )}
          </div>
        ))}
      </div>
    </section>
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

function normalizeToolDisplayName(name: string): string {
  const value = String(name || 'tool');
  const parts = value.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return value;
}
