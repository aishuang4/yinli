/** Team Runtime 独立右侧控制台。 */

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { AlertCircle, Bot, CheckCircle2, ChevronDown, ChevronRight, Circle, Clock3, Inbox, ListChecks, Loader2, Megaphone, Send, Users, X } from 'lucide-react';
import { Button, Tag } from '@agentscope-ai/design';
import { TextAreaField } from '../../components/form';
import { notifyError } from '../../utils/feedback';
import type { TeamConsoleState, TeamDomainEvent, TeamMemberRecord, TeamTaskRecord } from '../../types/teamRuntime';
import { useAppStore } from '../../stores/appStore';

type ConsoleTab = 'tasks' | 'mailbox' | 'events';

export function TeamConsolePanel({
  state,
  onClose,
}: {
  state: TeamConsoleState;
  onClose?: () => void;
}) {
  const activeProjectId = useAppStore((appState) => appState.activeProjectId);
  const snapshot = state.snapshot;
  const [selectedMemberId, setSelectedMemberId] = useState('leader');
  const [tab, setTab] = useState<ConsoleTab>('tasks');
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!snapshot) return;
    if (!snapshot.members.some((member) => member.id === selectedMemberId)) {
      setSelectedMemberId(snapshot.members[0]?.id || 'leader');
    }
  }, [selectedMemberId, snapshot]);

  const selectedMember = snapshot?.members.find((member) => member.id === selectedMemberId) || null;
  const selectedActiveTask = snapshot?.tasks.find((task) => task.id === selectedMember?.activeTaskId) || null;
  const tasks = useMemo(() => (
    snapshot?.tasks.filter((task) => (
      selectedMemberId === 'leader' ||
      task.assignedMemberId === selectedMemberId ||
      task.ownerMemberId === selectedMemberId
    )) || []
  ), [selectedMemberId, snapshot]);
  const mailbox = useMemo(() => (
    snapshot?.mailbox.filter((mail) => (
      selectedMemberId === 'leader' || mail.from === selectedMemberId || mail.to === selectedMemberId
    )) || []
  ), [selectedMemberId, snapshot]);
  const events = useMemo(() => state.events.filter((event) => (
    selectedMemberId === 'leader' || event.actorId === selectedMemberId
  )), [selectedMemberId, state.events]);

  if (!snapshot) return null;

  const sendMessage = async (recipient = selectedMember?.id || '') => {
    const content = message.trim();
    if (!content || !recipient) return;
    setSending(true);
    try {
      const params = new URLSearchParams({ projectId: activeProjectId });
      const response = await fetch(`/api/chat/runs/${snapshot.run.chatRunId}/team/mail?${params.toString()}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ recipient, content }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(String(error.detail || `HTTP ${response.status}`));
      }
      setMessage('');
      setTab('mailbox');
    } catch (error) {
      notifyError(error instanceof Error ? error.message : '消息发送失败');
    } finally {
      setSending(false);
    }
  };

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-[var(--color-border)] px-4">
        <Users size={16} className="text-[var(--color-accent)]" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]">Team Console</div>
          <div className="truncate text-xs text-[var(--color-text-tertiary)]">{phaseLabel(snapshot.run.phase)}</div>
        </div>
        <Tag size="small" color={snapshot.run.status === 'failed' ? 'red' : snapshot.run.status === 'completed' ? 'green' : snapshot.run.status === 'cancelled' ? 'orange' : 'blue'}>
          {snapshot.counts.succeeded}/{snapshot.counts.tasks}
        </Tag>
        {onClose && (
          <button type="button" className="icon-button h-8 w-8" onClick={onClose} aria-label="关闭 Team Console" title="关闭">
            <X size={14} />
          </button>
        )}
      </header>

      <div className="shrink-0 border-b border-[var(--color-border)] p-3">
        <div className="grid grid-cols-2 gap-2">
          {snapshot.members.map((member) => (
            <MemberButton
              key={member.id}
              member={member}
              selected={member.id === selectedMemberId}
              onClick={() => setSelectedMemberId(member.id)}
            />
          ))}
        </div>
      </div>

      <div className="flex shrink-0 border-b border-[var(--color-border)] px-3">
        <TabButton active={tab === 'tasks'} icon={<ListChecks size={13} />} label="任务" count={tasks.length} onClick={() => setTab('tasks')} />
        <TabButton active={tab === 'mailbox'} icon={<Inbox size={13} />} label="Mailbox" count={mailbox.length} onClick={() => setTab('mailbox')} />
        <TabButton active={tab === 'events'} icon={<Clock3 size={13} />} label="运行" count={events.length} onClick={() => setTab('events')} />
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3">
        {selectedMember && (
          <div className="mb-3 text-xs text-[var(--color-text-tertiary)]">
            <span className="font-medium text-[var(--color-text-secondary)]">{selectedMember.name}</span>
            <span> · {memberRoleLabel(selectedMember.role)}</span>
            {selectedMember.activeTaskId && (
              <span title={selectedMember.activeTaskId}> · {selectedActiveTask?.title || '当前任务'}</span>
            )}
          </div>
        )}
        {tab === 'tasks' && <TaskList tasks={tasks} allTasks={snapshot.tasks} />}
        {tab === 'mailbox' && (
          <div className="space-y-2">
            {mailbox.map((mail) => (
              <div key={mail.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span
                    className="truncate font-medium text-[var(--color-text-secondary)]"
                    title={`${mail.from} → ${mail.to}`}
                  >
                    {memberName(mail.from, snapshot.members)} → {memberName(mail.to, snapshot.members)}
                  </span>
                  <span className="shrink-0 text-[var(--color-text-tertiary)]" title={mail.kind}>{mailKindLabel(mail.kind)}</span>
                </div>
                {mail.subject && <div className="mt-1 text-xs font-medium text-[var(--color-text-primary)]">{mail.subject}</div>}
                <CollapsibleText
                  content={mail.content}
                  className="mt-1 text-[var(--color-text-secondary)]"
                />
              </div>
            ))}
            {mailbox.length === 0 && <EmptyText text="暂无消息" />}
          </div>
        )}
        {tab === 'events' && <EventList events={events} members={snapshot.members} tasks={snapshot.tasks} />}
      </div>
      <footer className="shrink-0 border-t border-[var(--color-border)] p-3">
        <div className="flex items-end gap-2">
          <div className="min-w-0 flex-1">
            <TextAreaField
              label={`发给 ${selectedMember?.name || '成员'}`}
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              rows={2}
              disabled={snapshot.run.status !== 'running'}
            />
          </div>
          <Button
            icon={<Megaphone size={14} />}
            loading={sending}
            disabled={!message.trim() || snapshot.run.status !== 'running'}
            onClick={() => void sendMessage('broadcast')}
            aria-label="广播 Mailbox 消息"
            title="广播给全体成员"
          />
          <Button
            type="primary"
            icon={<Send size={14} />}
            loading={sending}
            disabled={!message.trim() || !selectedMember || snapshot.run.status !== 'running'}
            onClick={() => void sendMessage()}
            aria-label="发送 Mailbox 消息"
          />
        </div>
      </footer>
    </aside>
  );
}

function MemberButton({ member, selected, onClick }: { member: TeamMemberRecord; selected: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex h-14 min-w-0 items-center gap-2 rounded-md border px-2 text-left ${selected ? 'border-[var(--color-accent)] bg-[var(--color-bg-primary)]' : 'border-transparent hover:bg-[var(--color-bg-hover)]'}`}
    >
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent)] text-white"><Bot size={13} /></div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium text-[var(--color-text-primary)]">{member.name}</div>
        <div className="mt-0.5 flex items-center gap-1 text-[11px] text-[var(--color-text-tertiary)]">
          {member.status === 'running' && <Loader2 size={10} className="animate-spin text-[var(--color-accent)]" />}
          {member.status === 'failed' && <AlertCircle size={10} className="text-[var(--color-error)]" />}
          {member.status === 'idle' && <CheckCircle2 size={10} className="text-[var(--color-success)]" />}
          <span>{memberStatusLabel(member.status)}</span>
        </div>
      </div>
    </button>
  );
}

function TabButton({ active, icon, label, count, onClick }: { active: boolean; icon: ReactNode; label: string; count: number; onClick: () => void }) {
  return <button type="button" onClick={onClick} className={`flex h-10 flex-1 items-center justify-center gap-1.5 border-b-2 text-xs ${active ? 'border-[var(--color-accent)] text-[var(--color-text-primary)]' : 'border-transparent text-[var(--color-text-tertiary)]'}`}>{icon}<span>{label}</span><span>{count}</span></button>;
}

function TaskList({ tasks, allTasks }: { tasks: TeamTaskRecord[]; allTasks: TeamTaskRecord[] }) {
  if (tasks.length === 0) return <EmptyText text="暂无任务" />;
  return <div className="space-y-2">{tasks.map((task) => (
    <div key={task.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
      <div className="flex items-start gap-2">
        <TaskStatusIcon task={task} />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-[var(--color-text-primary)]">{task.title}</div>
          <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">{taskStatusLabel(task)}</div>
          {task.briefPreview && (
            <CollapsibleText
              content={task.briefPreview}
              className="mt-2 text-[var(--color-text-secondary)]"
            />
          )}
          {task.dependsOn.length > 0 && (
            <div
              className="mt-1 break-words text-[11px] text-[var(--color-text-tertiary)]"
              title={task.dependsOn.join(', ')}
            >
              依赖 {task.dependsOn.map((taskId) => taskName(taskId, allTasks)).join('、')}
            </div>
          )}
          {task.resultPreview && (
            <div className="mt-2 border-l-2 border-[var(--color-success)] pl-2">
              <CollapsibleText
                content={task.resultPreview}
                className="text-[var(--color-text-primary)]"
              />
            </div>
          )}
          {task.failureReason && <div className="mt-1 text-xs text-[var(--color-error)]">{task.failureReason}</div>}
        </div>
      </div>
    </div>
  ))}</div>;
}

function EventList({
  events,
  members,
  tasks,
}: {
  events: TeamDomainEvent[];
  members: TeamMemberRecord[];
  tasks: TeamTaskRecord[];
}) {
  if (events.length === 0) return <EmptyText text="暂无运行事件" />;
  return <div className="space-y-1">{[...events].reverse().map((event) => (
    <div key={event.id} className="flex min-h-9 items-start gap-2 border-b border-[var(--color-border)] py-2 text-xs">
      <span className="w-7 shrink-0 text-right text-[var(--color-text-tertiary)]">#{event.seq}</span>
      <div className="min-w-0 flex-1">
        <div className="break-words text-[var(--color-text-secondary)]">{eventLabel(event.type)}</div>
        {(event.taskId || event.actorId) && (
          <div
            className="mt-0.5 truncate text-[11px] text-[var(--color-text-tertiary)]"
            title={[event.actorId, event.taskId].filter(Boolean).join(' · ')}
          >
            {event.actorId ? memberName(event.actorId, members) : ''}
            {event.taskId ? ` · ${taskName(event.taskId, tasks)}` : ''}
          </div>
        )}
        {eventDetail(event) && (
          <CollapsibleText
            content={eventDetail(event)}
            className="mt-1 text-[var(--color-text-primary)]"
            collapseAt={100}
          />
        )}
      </div>
    </div>
  ))}</div>;
}

function TaskStatusIcon({ task }: { task: TeamTaskRecord }) {
  if (task.status === 'succeeded') return <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-[var(--color-success)]" />;
  if (task.status === 'failed') return <AlertCircle size={14} className="mt-0.5 shrink-0 text-[var(--color-error)]" />;
  if (task.status === 'running') return <Loader2 size={14} className="mt-0.5 shrink-0 animate-spin text-[var(--color-accent)]" />;
  return <Circle size={14} className="mt-0.5 shrink-0 text-[var(--color-text-tertiary)]" />;
}

function CollapsibleText({
  content,
  className = '',
  collapseAt = 140,
}: {
  content: string;
  className?: string;
  collapseAt?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const shouldCollapse = content.length > collapseAt || content.split('\n').length > 3;

  return (
    <div className={className}>
      <div className={`whitespace-pre-wrap break-words text-xs leading-5 ${shouldCollapse && !expanded ? 'max-h-[3.75rem] overflow-hidden' : ''}`}>
        {content}
      </div>
      {shouldCollapse && (
        <button
          type="button"
          className="mt-1 flex h-6 items-center gap-1 text-[11px] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          title={expanded ? '收起内容' : '展开内容'}
        >
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <span>{expanded ? '收起' : '展开'}</span>
        </button>
      )}
    </div>
  );
}

function EmptyText({ text }: { text: string }) { return <div className="py-8 text-center text-xs text-[var(--color-text-tertiary)]">{text}</div>; }
function memberName(memberId: string, members: TeamMemberRecord[]) {
  if (memberId === 'user') return '用户';
  if (memberId === 'system') return '系统';
  return members.find((member) => member.id === memberId)?.name || (memberId === 'leader' ? 'Leader' : '未知成员');
}
function taskName(taskId: string, tasks: TeamTaskRecord[]) {
  return tasks.find((task) => task.id === taskId)?.title || '未知任务';
}
function memberRoleLabel(role: string) {
  return ({ leader: '负责人', member: '成员', research: '调研', researcher: '调研', review: '评审', reviewer: '评审' } as Record<string, string>)[role.toLowerCase()] || role;
}
function mailKindLabel(kind: string) {
  return ({ user_goal: '用户目标', user_intervention: '用户补充', task_assignment: '任务分派', result: '成果汇报', message: '协作消息' } as Record<string, string>)[kind] || '协作消息';
}
function phaseLabel(phase: string) { return ({ created: '正在创建', coordinating: 'Leader 正在组建团队', executing: '成员正在执行', synthesizing: 'Leader 正在综合', completed: '协作已完成', failed: '协作失败', cancelled: '协作已取消' } as Record<string, string>)[phase] || phase; }
function memberStatusLabel(status: string) { return ({ created: '已创建', idle: '空闲', running: '执行中', waiting: '等待', failed: '失败', stopped: '已停止' } as Record<string, string>)[status] || status; }
function taskStatusLabel(task: TeamTaskRecord) { if (task.blocked) return '等待依赖'; return ({ queued: '待领取', running: '进行中', succeeded: '已完成', failed: '失败', cancelled: '已取消' } as Record<string, string>)[task.status] || task.status; }
function eventDetail(event: TeamDomainEvent) {
  if (event.type.endsWith('.delta')) return String(event.payload.delta || '');
  if (event.type === 'member.tool.denied') {
    const reason = event.payload.reason === 'requires_user_confirmation' ? '需要人工确认' : '需要外部执行';
    return `${String(event.payload.name || 'tool')} · ${reason}`;
  }
  if (event.type.startsWith('member.iteration.')) {
    const lastTool = String(event.payload.lastTool || '');
    return lastTool ? `最后工具：${lastTool}` : '';
  }
  if (event.type.startsWith('member.tool.')) return String(event.payload.name || event.payload.state || '');
  if (event.type === 'task.completed') return String(event.payload.resultPreview || '');
  if (event.type === 'task.failed' || event.type === 'task.cancelled' || event.type === 'run.failed' || event.type === 'run.cancelled') return String(event.payload.message || '');
  return '';
}
function eventLabel(type: string) { return ({ 'run.created': 'TeamRun 已创建', 'run.phase.changed': '运行阶段变化', 'member.spawned': '成员已创建', 'member.status.changed': '成员状态变化', 'task.created': '任务已创建', 'task.claimed': '任务已领取', 'task.completed': '任务已完成', 'task.failed': '任务失败', 'task.cancelled': '任务已取消', 'task.unlocked': '任务依赖已解锁', 'mail.sent': 'Mailbox 消息已投递', 'mail.read': 'Mailbox 消息已读取', 'member.thinking.delta': '成员正在分析', 'member.tool.started': '成员调用工具', 'member.tool.completed': '成员工具完成', 'member.tool.failed': '成员工具失败', 'member.tool.denied': '成员工具已拒绝', 'member.iteration.recovery': '成员正在收敛结果', 'member.iteration.exhausted': '成员恢复预算已耗尽', 'member.output.delta': '成员输出更新', 'run.completed': 'TeamRun 已完成', 'run.failed': 'TeamRun 失败', 'run.cancelled': 'TeamRun 已取消' } as Record<string, string>)[type] || type; }
