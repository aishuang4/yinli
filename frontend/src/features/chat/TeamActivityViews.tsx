import { AlertCircle, Bot, CheckCircle2, ChevronRight, Clock3, GitBranch, Loader2, Users, X } from 'lucide-react';
import { Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import type { TeamActivityInfo } from '../../hooks/useEventStream';
import { normalizeStoredToolCalls } from './formatters';
import { ToolCallBlock } from './ToolArtifacts';

/**
 * TeamActivityPanel — Team 协作过程展示
 */
export function TeamActivityPanel({
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

      <div className="grid items-stretch gap-2 px-3 pb-3 md:grid-cols-2">
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

        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
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
                className={`ui-card-transition flex h-[92px] min-w-0 flex-col rounded-md border bg-[var(--color-bg-secondary)] p-2 text-left ${
                  selected ? 'border-[var(--color-accent)] shadow-sm' : 'border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 truncate text-xs font-medium text-[var(--color-text-primary)]">
                    {worker.agentName}
                  </div>
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: statusColor }} />
                </div>
                <div className="mt-1 flex h-6 min-w-0 items-center gap-1 overflow-hidden">
                  <Tag size="small" color={worker.executionMode === 'depends_on' ? 'blue' : 'transparent'}>
                    {worker.executionMode === 'depends_on' ? '依赖执行' : '并行'}
                  </Tag>
                  {worker.dependencyOutputCount > 0 && (
                    <Tag size="small" color="green">收到 {worker.dependencyOutputCount}</Tag>
                  )}
                </div>
                <div className="mt-auto min-w-0 truncate text-[11px] text-[var(--color-text-tertiary)]">
                  {worker.waitReason || (dependencyNames.length > 0 ? `依赖：${dependencyNames.join(' -> ')}` : worker.currentEvent || worker.detail || '')}
                </div>
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
  const summaryText = activity.report || activity.output || activity.detail || activity.currentEvent || '';
  const reviewLabel = activity.reportReviewStatus && activity.role !== 'leader'
    ? (activity.reportReviewStatus === 'accepted' ? '报告可用' : '需要补充')
    : '';
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
      className={`ui-card-transition flex h-[132px] min-w-0 flex-col rounded-md bg-[var(--color-bg-primary)] p-3 text-left ${
        selected ? 'ring-1 ring-[var(--color-accent)]' : 'hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)]'
      }`}
    >
      <div className="mb-2 flex h-8 w-full shrink-0 items-center gap-2 text-left">
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
      <div
        className="h-5 min-w-0 truncate text-sm leading-5 text-[var(--color-text-secondary)]"
        title={activity.assignment || activity.task || ''}
      >
        {activity.assignment || activity.task || ''}
      </div>
      <div
        className="mt-1 h-5 min-w-0 truncate text-xs leading-5 text-[var(--color-text-tertiary)]"
        title={summaryText}
      >
        {summaryText}
      </div>
      <div className="mt-auto flex h-7 min-w-0 items-center gap-1 overflow-hidden pt-1">
        {reviewLabel && (
          <Tag size="small" color={activity.reportReviewStatus === 'accepted' ? 'green' : 'orange'}>
            {reviewLabel}
          </Tag>
        )}
        {toolCalls.length > 0 && (
          <>
          {toolCalls.slice(0, 2).map((toolCall) => (
            <span
              key={toolCall.id}
              className="flex h-[22px] min-w-0 max-w-[160px] items-center truncate rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-1.5 text-xs text-[var(--color-text-tertiary)]"
              title={toolCall.name}
            >
              {toolCall.name}
            </span>
          ))}
          {toolCalls.length > 2 && (
            <span className="flex h-[22px] shrink-0 items-center rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-1.5 text-xs text-[var(--color-text-tertiary)]">
              +{toolCalls.length - 2}
            </span>
          )}
          </>
        )}
      </div>
    </button>
  );
}

export function TeamActivitySidebar({
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
            {activity.reportReviewStatus && (
              <Tag size="small" color={activity.reportReviewStatus === 'accepted' ? 'green' : 'orange'}>
                {activity.reportReviewStatus === 'accepted' ? '报告可用' : '需要补充'}
              </Tag>
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
          <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">分派任务</div>
          <div className="whitespace-pre-wrap break-words text-sm text-[var(--color-text-secondary)]">
            {activity.assignment || activity.task}
          </div>
          {activity.expectedOutput && (
            <div className="mt-3">
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">期望输出</div>
              <div className="whitespace-pre-wrap break-words text-xs text-[var(--color-text-secondary)]">
                {activity.expectedOutput}
              </div>
            </div>
          )}
          {activity.constraints && (
            <div className="mt-3">
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">约束</div>
              <div className="whitespace-pre-wrap break-words text-xs text-[var(--color-text-tertiary)]">
                {activity.constraints}
              </div>
            </div>
          )}
          {activity.detail && (
            <div className="mt-2 whitespace-pre-wrap break-words text-xs text-[var(--color-text-tertiary)]">
              {activity.detail}
            </div>
          )}
          {activity.skippedReason && (
            <div className="mt-2 whitespace-pre-wrap break-words text-xs text-[var(--color-warning)]">
              跳过原因：{activity.skippedReason}
            </div>
          )}
          {activity.reportReviewStatus && activity.role !== 'leader' && (
            <div className="mt-3 rounded-md bg-[var(--color-bg-secondary)] p-2">
              <div className="mb-1 text-xs text-[var(--color-text-tertiary)]">报告质量</div>
              <div className="text-xs text-[var(--color-text-secondary)]">
                {activity.reportReviewStatus === 'accepted' ? '报告可用于 Leader 汇总' : '报告信息不足，Leader 会按缺口谨慎汇总'}
              </div>
              {activity.reportReviewNotes && (
                <div className="mt-1 whitespace-pre-wrap break-words text-xs text-[var(--color-text-tertiary)]">
                  {activity.reportReviewNotes}
                </div>
              )}
              {activity.reportReviewGaps.length > 0 && (
                <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-[var(--color-warning)]">
                  {activity.reportReviewGaps.map((gap, index) => (
                    <li key={`${gap}-${index}`}>{gap}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>

        <section className="rounded-lg bg-[var(--color-bg-primary)] p-3">
          <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">
            {activity.role === 'leader' ? '输出' : 'Worker report'}
          </div>
          {activity.report || activity.output ? (
            <div className="max-h-80 overflow-auto text-xs text-[var(--color-text-secondary)]">
              <MarkdownContent content={activity.report || activity.output} />
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

function getTeamActivityStatusText(status: TeamActivityInfo['status']) {
  if (status === 'running') return '执行中';
  if (status === 'waiting') return '等待确认';
  if (status === 'completed') return '完成';
  if (status === 'failed') return '失败';
  if (status === 'skipped') return '跳过';
  return '等待';
}
