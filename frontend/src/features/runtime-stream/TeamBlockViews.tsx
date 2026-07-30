/** Team Block 摘要、实体 fallback renderer 与完整 Team Console。 */

import { useContext, useMemo, useState } from 'react';
import { Input, Select, Tooltip } from '@/components/design';
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronRight,
  Circle,
  Clock3,
  Loader2,
  Mail,
  MessageSquareText,
  PanelRightOpen,
  Send,
  Terminal,
  Users,
  X,
} from 'lucide-react';
import { MarkdownContent } from '../../components/MarkdownContent';
import { resolveApiUrl } from '../../utils/apiUrl';
import type { DisplayBlock, DisplaySnapshot } from './protocol';
import { TeamRuntimeContext } from './TeamRuntimeContext';
import {
  selectTaskActivityBlocks,
  selectTeamBlocks,
  selectTeamRoot,
  type TeamRecord,
  type TeamRecordBlock,
} from './teamSelectors';

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function record(value: unknown): TeamRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as TeamRecord : {};
}

function statusText(status: string): string {
  return ({
    created: '准备中',
    idle: '空闲',
    queued: '排队中',
    running: '运行中',
    coordinating: '规划任务',
    executing: '执行任务',
    synthesizing: '综合结果',
    recovering: '正在收口',
    succeeded: '已完成',
    completed: '已完成',
    stopped: '已结束',
    failed: '失败',
    cancelled: '已取消',
    delivered: '已送达',
    read: '已读取',
    acted: '已处理',
  } as Record<string, string>)[status] || status || '未知';
}

function statusTone(status: string): string {
  if (status === 'failed') return 'text-[var(--color-error)]';
  if (status === 'cancelled') return 'text-[var(--color-warning)]';
  if (['completed', 'succeeded', 'stopped', 'read', 'acted'].includes(status)) {
    return 'text-[var(--color-success)]';
  }
  if (['running', 'coordinating', 'executing', 'synthesizing', 'recovering'].includes(status)) {
    return 'text-[var(--color-accent-text)]';
  }
  return 'text-[var(--color-text-tertiary)]';
}

function StatusIcon({ status }: { status: string }) {
  if (['running', 'coordinating', 'executing', 'synthesizing', 'recovering'].includes(status)) {
    return <Loader2 size={14} className="animate-spin" />;
  }
  if (['completed', 'succeeded', 'stopped'].includes(status)) return <CheckCircle2 size={14} />;
  if (status === 'failed') return <AlertCircle size={14} />;
  if (status === 'queued') return <Clock3 size={14} />;
  return <Circle size={14} />;
}

export function TeamBlockView({ block }: { block: DisplayBlock<TeamRecord> }) {
  const runtime = useContext(TeamRuntimeContext);
  const snapshot = runtime?.snapshot;
  const counts = record(block.data.counts);
  const status = text(block.data.phase, text(block.data.status, block.phase === 'open' ? 'running' : 'completed'));
  const members = useMemo(
    () => snapshot ? selectTeamBlocks(snapshot, block.id, 'team-member') : [],
    [block.id, snapshot],
  );
  const tasks = useMemo(
    () => snapshot ? selectTeamBlocks(snapshot, block.id, 'team-task') : [],
    [block.id, snapshot],
  );
  const recentActivity = useMemo(() => {
    if (!snapshot) return [];
    const activity = selectTeamBlocks(snapshot, block.id, 'team-activity');
    const tools = selectTeamBlocks(snapshot, block.id, 'tool');
    const order = new Map(snapshot.order.map((id, index) => [id, index]));
    return [...activity, ...tools]
      .sort((left, right) => (order.get(left.id) || 0) - (order.get(right.id) || 0))
      .slice(-3);
  }, [block.id, snapshot]);
  const completed = Number(counts.succeeded || 0);
  const total = Number(counts.tasks || tasks.length);
  const progress = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  const active = block.phase === 'open';
  return (
    <section className="overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
      <button
        type="button"
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
        onClick={() => runtime?.onOpenTeam(block.id)}
        aria-label="打开 Team Console"
      >
        <span className={`relative flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-[var(--color-bg-primary)] ${statusTone(status)}`}>
          {active ? <span className="absolute inset-0 animate-pulse rounded-md ring-1 ring-[var(--color-border-focus)]" /> : null}
          <Users size={15} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
            Team 协作
            {active ? <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--color-accent)]" /> : null}
          </span>
          <span className="mt-0.5 block text-xs text-[var(--color-text-tertiary)]">
            {statusText(status)} · {completed}/{total} 个任务完成
          </span>
        </span>
        {active ? <Loader2 size={14} className="animate-spin text-[var(--color-accent-text)]" /> : null}
        <PanelRightOpen size={15} className="text-[var(--color-text-tertiary)]" />
      </button>

      <div className="border-t border-[var(--color-border)] px-3 py-3">
        <div className="h-1 overflow-hidden rounded-full bg-[var(--color-bg-primary)]">
          <div
            className={`h-full rounded-full bg-[var(--color-accent)] transition-[width] duration-500 ${active ? 'animate-pulse' : ''}`}
            style={{ width: `${progress}%` }}
          />
        </div>

        <div className="mt-3 flex min-h-8 flex-wrap items-center gap-2">
          {members.map((member) => {
            const memberStatus = text(member.data.status, member.phase === 'open' ? 'running' : text(member.outcome));
            return (
              <span
                key={member.id}
                className="inline-flex h-7 max-w-40 items-center gap-1.5 rounded-md bg-[var(--color-bg-primary)] px-2 text-xs text-[var(--color-text-secondary)] transition-colors duration-300"
                title={`${text(member.data.name, 'Member')} · ${statusText(memberStatus)}`}
              >
                <span className={statusTone(memberStatus)}><StatusIcon status={memberStatus} /></span>
                <span className="truncate">{text(member.data.name, 'Member')}</span>
              </span>
            );
          })}
          {!members.length ? (
            <span className="inline-flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
              <Loader2 size={13} className="animate-spin" />Leader 正在组建团队
            </span>
          ) : null}
        </div>

        <div className="mt-2 space-y-1">
          {tasks.slice(0, 4).map((task) => {
            const taskStatus = text(task.data.status, task.phase === 'open' ? 'running' : text(task.outcome));
            return (
              <div key={task.id} className="flex min-h-7 items-center gap-2 rounded px-1.5 text-xs transition-colors duration-300 hover:bg-[var(--color-bg-hover)]">
                <span className={`shrink-0 ${statusTone(taskStatus)}`}><StatusIcon status={taskStatus} /></span>
                <span className="min-w-0 flex-1 truncate text-[var(--color-text-secondary)]">{text(task.data.title, '未命名任务')}</span>
                <span className={`shrink-0 ${statusTone(taskStatus)}`}>{statusText(taskStatus)}</span>
              </div>
            );
          })}
          {tasks.length > 4 ? <div className="px-1.5 text-[11px] text-[var(--color-text-tertiary)]">另有 {tasks.length - 4} 个任务</div> : null}
          {!tasks.length && members.length ? <div className="px-1.5 text-xs text-[var(--color-text-tertiary)]">Leader 正在拆分任务</div> : null}
        </div>

        {recentActivity.length ? (
          <div className="mt-2 border-t border-[var(--color-border)] pt-2" aria-label="Team 实时活动">
            {recentActivity.map((activity) => <LiveActivityLine key={activity.id} block={activity} members={members} />)}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function LiveActivityLine({ block, members }: { block: TeamRecordBlock; members: TeamRecordBlock[] }) {
  const memberNames = new Map(members.map((member) => [
    text(member.data.memberId, text(member.data.id)),
    text(member.data.name, 'Member'),
  ]));
  const isTool = block.kind === 'tool';
  const memberId = text(block.data.memberId);
  const content = isTool
    ? text(block.data.name, 'Tool')
    : text(block.data.content).replace(/\s+/g, ' ').trim();
  const label = content || (block.phase === 'open' ? '正在处理' : '过程已完成');
  return (
    <div className="flex min-h-6 items-center gap-2 px-1.5 text-[11px] text-[var(--color-text-tertiary)]">
      {block.phase === 'open'
        ? <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-[var(--color-accent)]" />
        : <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-border)]" />}
      <span className="max-w-24 shrink-0 truncate text-[var(--color-text-secondary)]">{memberNames.get(memberId) || (memberId === 'leader' ? 'Leader' : 'Team')}</span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </div>
  );
}

export function TeamMemberBlockView({ block }: { block: DisplayBlock<TeamRecord> }) {
  return <EntityFallback icon={<Bot size={14} />} title={text(block.data.name, 'Member')} block={block} />;
}

export function TeamTaskBlockView({ block }: { block: DisplayBlock<TeamRecord> }) {
  return <EntityFallback icon={<CheckCircle2 size={14} />} title={text(block.data.title, 'Task')} block={block} />;
}

export function TeamActivityBlockView({ block }: { block: DisplayBlock<TeamRecord> }) {
  return <EntityFallback icon={<MessageSquareText size={14} />} title="Member 过程" block={block} />;
}

export function TeamMailBlockView({ block }: { block: DisplayBlock<TeamRecord> }) {
  return <EntityFallback icon={<Mail size={14} />} title={text(block.data.subject, 'Mailbox')} block={block} />;
}

function EntityFallback({
  icon,
  title,
  block,
}: {
  icon: React.ReactNode;
  title: string;
  block: DisplayBlock<TeamRecord>;
}) {
  const status = text(block.data.status, block.phase === 'open' ? 'running' : text(block.outcome, 'completed'));
  return (
    <div className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-[var(--color-text-secondary)]">
      <span className={statusTone(status)}>{icon}</span>
      <span className="min-w-0 flex-1 truncate">{title}</span>
      <span className={statusTone(status)}>{statusText(status)}</span>
    </div>
  );
}

type TeamTab = 'tasks' | 'members' | 'mail';

export function TeamConsole({ snapshot, teamBlockId }: { snapshot: DisplaySnapshot; teamBlockId: string }) {
  const runtime = useContext(TeamRuntimeContext);
  const root = selectTeamRoot(snapshot, teamBlockId);
  const members = useMemo(() => selectTeamBlocks(snapshot, teamBlockId, 'team-member'), [snapshot, teamBlockId]);
  const tasks = useMemo(() => selectTeamBlocks(snapshot, teamBlockId, 'team-task'), [snapshot, teamBlockId]);
  const mails = useMemo(() => selectTeamBlocks(snapshot, teamBlockId, 'team-mail'), [snapshot, teamBlockId]);
  const [tab, setTab] = useState<TeamTab>('tasks');
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  if (!root) return null;
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) || null;
  const rootStatus = text(root.data.phase, text(root.data.status, root.phase === 'open' ? 'running' : 'completed'));
  const counts = record(root.data.counts);

  return (
    <aside
      className="fixed bottom-3 right-3 top-3 z-50 flex w-[min(520px,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-primary)] shadow-2xl"
      aria-label="Team Console"
    >
      <header className="flex shrink-0 items-start gap-3 border-b border-[var(--color-border)] px-4 py-3">
        <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-[var(--color-bg-secondary)] ${statusTone(rootStatus)}`}>
          <Users size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-[var(--color-text-primary)]">Team Console</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
            <span className={statusTone(rootStatus)}>{statusText(rootStatus)}</span>
            <span>{Number(counts.succeeded || 0)}/{Number(counts.tasks || 0)} 个任务完成</span>
            <span>{Number(counts.members || members.length)} 名成员</span>
          </div>
        </div>
        <Tooltip title="关闭 Team Console" placement="left" mode="dark">
          <button
            type="button"
            className="icon-button flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            onClick={runtime?.onCloseTeam}
            aria-label="关闭 Team Console"
          >
            <X size={16} />
          </button>
        </Tooltip>
      </header>

      <nav className="flex h-10 shrink-0 border-b border-[var(--color-border)] px-3" aria-label="Team Console 视图">
        {([
          ['tasks', `任务 ${tasks.length}`],
          ['members', `成员 ${members.length}`],
          ['mail', `Mailbox ${mails.length}`],
        ] as Array<[TeamTab, string]>).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={`border-b-2 px-3 text-xs font-medium ${tab === value ? 'border-[var(--color-text-primary)] text-[var(--color-text-primary)]' : 'border-transparent text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]'}`}
            onClick={() => setTab(value)}
          >
            {label}
          </button>
        ))}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'tasks' ? (
          selectedTask
            ? <TaskDetail snapshot={snapshot} task={selectedTask} members={members} onBack={() => setSelectedTaskId(null)} />
            : <TaskList tasks={tasks} members={members} onSelect={setSelectedTaskId} />
        ) : null}
        {tab === 'members' ? <MemberList members={members} tasks={tasks} /> : null}
        {tab === 'mail' ? <MailboxPanel root={root} members={members} mails={mails} /> : null}
      </div>
    </aside>
  );
}

function TaskList({ tasks, members, onSelect }: { tasks: TeamRecordBlock[]; members: TeamRecordBlock[]; onSelect: (id: string) => void }) {
  if (!tasks.length) return <EmptyState text="Leader 正在拆分任务" />;
  const names = new Map(members.map((member) => [text(member.data.memberId, text(member.data.id)), text(member.data.name, 'Member')]));
  return (
    <div>
      {tasks.map((task) => {
        const status = text(task.data.status, task.phase === 'open' ? 'running' : text(task.outcome));
        const owner = text(task.data.ownerMemberId, text(task.data.assignedMemberId));
        return (
          <button
            type="button"
            key={task.id}
            className="flex w-full items-start gap-3 border-b border-[var(--color-border)] px-4 py-3 text-left hover:bg-[var(--color-bg-hover)]"
            onClick={() => onSelect(task.id)}
          >
            <span className={`mt-0.5 shrink-0 ${statusTone(status)}`}><StatusIcon status={status} /></span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm text-[var(--color-text-primary)]">{text(task.data.title, '未命名任务')}</span>
              <span className="mt-1 block text-xs text-[var(--color-text-tertiary)]">
                {owner ? names.get(owner) || owner : '等待分配'} · {statusText(status)}
              </span>
            </span>
            <ChevronRight size={15} className="mt-0.5 shrink-0 text-[var(--color-text-tertiary)]" />
          </button>
        );
      })}
    </div>
  );
}

function TaskDetail({ snapshot, task, members, onBack }: { snapshot: DisplaySnapshot; task: TeamRecordBlock; members: TeamRecordBlock[]; onBack: () => void }) {
  const activities = selectTaskActivityBlocks(snapshot, task.id);
  const names = new Map(members.map((member) => [text(member.data.memberId, text(member.data.id)), text(member.data.name, 'Member')]));
  const owner = text(task.data.ownerMemberId, text(task.data.assignedMemberId));
  const status = text(task.data.status, task.phase === 'open' ? 'running' : text(task.outcome));
  return (
    <div>
      <button type="button" className="flex w-full items-center gap-2 border-b border-[var(--color-border)] px-4 py-2.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]" onClick={onBack}>
        <ChevronRight size={14} className="rotate-180" />返回任务列表
      </button>
      <section className="border-b border-[var(--color-border)] px-4 py-4">
        <div className="flex items-start gap-2">
          <span className={`mt-0.5 ${statusTone(status)}`}><StatusIcon status={status} /></span>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{text(task.data.title, '未命名任务')}</h3>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">{owner ? names.get(owner) || owner : '等待分配'} · {statusText(status)}</div>
          </div>
        </div>
        {text(task.data.briefPreview) ? <div className="mt-3 text-sm leading-6 text-[var(--color-text-secondary)]"><MarkdownContent content={text(task.data.briefPreview)} /></div> : null}
      </section>
      <section>
        {activities.map((activity) => <ActivityRow key={activity.id} block={activity} />)}
        {!activities.length ? <EmptyState text="暂无执行过程" /> : null}
      </section>
      {text(task.data.resultPreview) ? (
        <section className="border-t border-[var(--color-border)] px-4 py-4">
          <div className="mb-2 text-xs font-medium text-[var(--color-text-tertiary)]">任务结果</div>
          <div className="text-sm text-[var(--color-text-secondary)]"><MarkdownContent content={text(task.data.resultPreview)} /></div>
        </section>
      ) : null}
    </div>
  );
}

function ActivityRow({ block }: { block: DisplayBlock }) {
  const data = record(block.data);
  const isTool = block.kind === 'tool';
  const content = isTool
    ? text(data.outputPreview, text(data.error, text(data.inputPreview)))
    : text(data.content);
  return (
    <div className="border-b border-[var(--color-border)] px-4 py-3">
      <div className="flex items-center gap-2 text-xs font-medium text-[var(--color-text-secondary)]">
        {isTool ? <Terminal size={14} /> : <MessageSquareText size={14} />}
        <span className="min-w-0 flex-1 truncate">{isTool ? text(data.name, 'Tool') : text(data.activityType, '过程')}</span>
        {block.phase === 'open' ? <Loader2 size={13} className="animate-spin" /> : null}
      </div>
      {content ? <div className="mt-2 max-h-56 overflow-auto text-xs leading-5 text-[var(--color-text-tertiary)]"><MarkdownContent content={content} /></div> : null}
    </div>
  );
}

function MemberList({ members, tasks }: { members: TeamRecordBlock[]; tasks: TeamRecordBlock[] }) {
  if (!members.length) return <EmptyState text="暂无成员" />;
  const taskTitles = new Map(tasks.map((task) => [text(task.data.id), text(task.data.title, 'Task')]));
  return (
    <div>
      {members.map((member) => {
        const status = text(member.data.status, member.phase === 'open' ? 'running' : text(member.outcome));
        const activeTaskId = text(member.data.activeTaskId);
        return (
          <div key={member.id} className="flex items-start gap-3 border-b border-[var(--color-border)] px-4 py-3">
            <span className={`mt-0.5 ${statusTone(status)}`}><Bot size={15} /></span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-[var(--color-text-primary)]">{text(member.data.name, 'Member')}</div>
              <div className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">{text(member.data.role, 'member')}</div>
              {activeTaskId ? <div className="mt-1 truncate text-xs text-[var(--color-text-secondary)]">{taskTitles.get(activeTaskId) || activeTaskId}</div> : null}
            </div>
            <span className={`shrink-0 text-xs ${statusTone(status)}`}>{statusText(status)}</span>
          </div>
        );
      })}
    </div>
  );
}

function MailboxPanel({ root, members, mails }: { root: TeamRecordBlock; members: TeamRecordBlock[]; mails: TeamRecordBlock[] }) {
  const [recipient, setRecipient] = useState('broadcast');
  const [content, setContent] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const active = root.phase === 'open' && text(root.data.status, 'running') === 'running';
  const memberNames = new Map(members.map((member) => [text(member.data.memberId, text(member.data.id)), text(member.data.name, 'Member')]));

  const send = async () => {
    const runId = text(root.data.chatRunId);
    const projectId = text(root.data.projectId, 'default');
    if (!runId || !content.trim() || sending) return;
    setSending(true);
    setError('');
    try {
      const response = await fetch(resolveApiUrl(`/api/chat/runs/${encodeURIComponent(runId)}/team/mail?projectId=${encodeURIComponent(projectId)}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ recipient, content: content.trim() }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || `发送失败 (${response.status})`);
      }
      setContent('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '发送失败');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <div className="min-h-0 flex-1">
        {mails.map((mail) => {
          const from = text(mail.data.from);
          const to = text(mail.data.to);
          return (
            <div key={mail.id} className="border-b border-[var(--color-border)] px-4 py-3">
              <div className="flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
                <Mail size={13} />
                <span>{memberNames.get(from) || from}</span>
                <ChevronRight size={12} />
                <span>{memberNames.get(to) || to}</span>
                <span className="ml-auto">{statusText(text(mail.data.deliveryStatus))}</span>
              </div>
              {text(mail.data.subject) ? <div className="mt-1.5 text-sm font-medium text-[var(--color-text-primary)]">{text(mail.data.subject)}</div> : null}
              <div className="mt-1 whitespace-pre-wrap text-sm leading-5 text-[var(--color-text-secondary)]">{text(mail.data.content)}</div>
            </div>
          );
        })}
        {!mails.length ? <EmptyState text="暂无 Mailbox 消息" /> : null}
      </div>
      {active ? (
        <div className="sticky bottom-0 border-t border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3">
          <Select
            value={recipient}
            onChange={(value) => setRecipient(String(value))}
            options={[
              { label: '广播给全部成员', value: 'broadcast' },
              ...members.map((member) => ({
                label: text(member.data.name, 'Member'),
                value: text(member.data.memberId, text(member.data.id)),
              })),
            ]}
            style={{ width: '100%' }}
          />
          <div className="mt-2 flex items-end gap-2">
            <Input.TextArea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              placeholder="向 Team 发送补充信息"
              autoSize={{ minRows: 2, maxRows: 5 }}
              maxLength={20_000}
            />
            <button
              type="button"
              className="icon-button flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] disabled:cursor-not-allowed disabled:opacity-40"
              onClick={() => void send()}
              disabled={!content.trim() || sending}
              aria-label="发送 Team Mailbox 消息"
            >
              {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
            </button>
          </div>
          {error ? <div className="mt-2 text-xs text-[var(--color-error)]">{error}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

function EmptyState({ text: value }: { text: string }) {
  return <div className="px-4 py-10 text-center text-sm text-[var(--color-text-tertiary)]">{value}</div>;
}
