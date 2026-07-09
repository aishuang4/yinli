/**
 * WorkflowPage — Dynamic Workflow 独立工作空间。
 *
 * 以 Execution Theater 表达“目标被编译、派发、汇合、验证、产出”的过程。
 */

import { useState, type ReactNode } from 'react';
import {
  Braces,
  CheckCircle2,
  ChevronRight,
  Circle,
  Code2,
  FileText,
  GitBranch,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@agentscope-ai/design';
import { TextAreaField } from '../components/form';
import { useWorkflowRuns } from '../hooks/useWorkflowRuns';
import type { WorkflowEvent, WorkflowNode, WorkflowRunDetail, WorkflowRunStatus, WorkflowScript } from '../types/workflow';
import { cn } from '../utils/cn';
import './WorkflowPage.css';

const SAMPLE_GOAL = '审计当前项目的记忆系统，要求并行扫描后端、前端、文档和测试，交叉验证风险并生成可执行修复计划。';

type InspectorTab = 'stage' | 'variables' | 'agents' | 'events' | 'script';

export function WorkflowPage() {
  const {
    runs,
    selectedRunId,
    detail,
    loading,
    detailLoading,
    creating,
    starting,
    error,
    setSelectedRunId,
    loadRuns,
    createWorkflow,
    startWorkflow,
  } = useWorkflowRuns();
  const [goal, setGoal] = useState(SAMPLE_GOAL);

  const handleCreate = async () => {
    const normalizedGoal = goal.trim();
    if (!normalizedGoal) return;
    const created = await createWorkflow({ goal: normalizedGoal });
    if (created) setGoal('');
  };

  return (
    <div className="workflow-page">
      <section className="workflow-sidebar">
        <div className="workflow-sidebar__header">
          <div>
            <h2>任务流</h2>
            <p>编译、派发、汇合、验证</p>
          </div>
          <Button size="small" icon={<RefreshCw size={14} />} onClick={() => void loadRuns()} />
        </div>

        <div className="workflow-create">
          <TextAreaField
            label="目标"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="输入复杂目标，编译为可执行 workflow"
            rows={5}
            className="workflow-create__field"
          />
          <Button
            type="primary"
            icon={<Plus size={14} />}
            loading={creating}
            disabled={!goal.trim()}
            onClick={() => void handleCreate()}
          >
            编译 Workflow
          </Button>
        </div>

        <div className="workflow-run-list">
          {error && <div className="workflow-error">{error}</div>}
          {loading ? (
            <div className="workflow-loading"><Spinner /></div>
          ) : runs.length === 0 ? (
            <Empty type="noData" description="暂无任务流" />
          ) : (
            runs.map((run) => (
              <button
                key={run.id}
                type="button"
                className={cn('workflow-run-item', selectedRunId === run.id && 'workflow-run-item--active')}
                onClick={() => setSelectedRunId(run.id)}
              >
                <span className="workflow-run-item__title">{run.title}</span>
                <span className="workflow-run-item__meta">
                  <WorkflowStatusTag status={run.status} />
                  <span>{formatDateTime(run.updatedAt)}</span>
                </span>
              </button>
            ))
          )}
        </div>
      </section>

      <section className="workflow-main workflow-main--theater">
        {detailLoading && !detail ? (
          <div className="workflow-empty"><Spinner /></div>
        ) : !detail ? (
          <div className="workflow-empty">
            <Sparkles size={26} />
            <div>创建或选择一个任务流</div>
          </div>
        ) : (
          <TheaterWorkspace
            run={detail}
            starting={starting}
            onStart={() => void startWorkflow(detail.id)}
          />
        )}
      </section>
    </div>
  );
}

function TheaterWorkspace({
  run,
  starting,
  onStart,
}: {
  run: WorkflowRunDetail;
  starting: boolean;
  onStart: () => void;
}) {
  return (
    <div className="workflow-theater-layout">
      <header className="theater-header">
        <div className="theater-header__copy">
          <div className="workflow-eyebrow">
            <Sparkles size={14} />
            {run.status === 'draft' ? 'Workflow 已编译' : 'Workflow Runtime'}
          </div>
          <h1>{run.title}</h1>
          <p>{run.goal}</p>
        </div>
        <div className="theater-header__actions">
          <CompileBadge script={run.script} />
          <Button
            type="primary"
            icon={<Play size={14} />}
            loading={starting}
            disabled={run.status === 'running' || run.status === 'completed'}
            onClick={onStart}
          >
            {run.status === 'draft' ? '批准运行' : '继续运行'}
          </Button>
        </div>
      </header>

      <main className="theater-grid">
        <StageRail run={run} />
        <ExecutionTheater run={run} />
        <WorkflowInspector run={run} />
      </main>

      <AgentStream events={run.events} />
    </div>
  );
}

function CompileBadge({ script }: { script?: WorkflowScript }) {
  if (!script) return null;
  return (
    <div className="compile-badge">
      <span>{script.stages.length} stages</span>
      <span>{script.estimate.agents} agents</span>
      <span>1 barrier</span>
      <span>2 vars</span>
      <span>{formatNumber(script.estimate.tokens)} tokens</span>
    </div>
  );
}

function StageRail({ run }: { run: WorkflowRunDetail }) {
  return (
    <aside className="stage-rail-panel">
      <div className="stage-rail-panel__title">Stage Rail</div>
      <div className="stage-rail-list">
        {run.nodes.map((node, index) => (
          <div
            key={node.id}
            className={cn(
              'stage-step',
              `stage-step--${node.status}`,
              (run.currentNodeId === node.id || node.status === 'running') && 'stage-step--active',
            )}
          >
            <div className="stage-step__dot">
              {node.status === 'completed' ? <CheckCircle2 size={15} /> : <Circle size={12} />}
            </div>
            <div className="stage-step__body">
              <span>{index + 1}. {node.type}</span>
              <strong>{node.title}</strong>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}

function ExecutionTheater({ run }: { run: WorkflowRunDetail }) {
  const script = run.script;
  const fanout = script?.stages.find((stage) => stage.type === 'fanout');
  const variableReady = hasEvent(run.events, 'VARIABLE_UPDATED');
  const verifyDone = run.nodes.some((node) => node.type === 'verifier' && node.status === 'completed');
  const reportDone = run.nodes.some((node) => node.type === 'summary' && node.status === 'completed');

  return (
    <section className="execution-theater">
      <div className="execution-theater__ambient" />

      <div className="theater-flow">
        <TheaterNode
          icon={<Sparkles size={18} />}
          label="Goal"
          title="用户目标"
          detail={run.status === 'draft' ? '已编译，等待批准' : 'Runtime 已接管'}
          state="ready"
        />
        <FlowArrow />
        <TheaterNode
          icon={<Code2 size={18} />}
          label="Compile"
          title="Workflow Script"
          detail="阶段、并发、变量和权限已确定"
          state="ready"
        />
        <FlowArrow />

        <div className="fanout-stage">
          <div className="fanout-stage__header">
            <GitBranch size={16} />
            <span>Fan-out</span>
            <strong>{fanout?.items?.length || 0} Agent tasks</strong>
          </div>
          <div className="agent-orbit">
            {(fanout?.items || []).map((item) => (
              <AgentOrbitCard
                key={item.key}
                name={item.agentName}
                label={item.key}
                status={taskStatus(item.key, run.events)}
              />
            ))}
          </div>
        </div>

        <FlowArrow />
        <VariableNode
          name="scanReports"
          detail="四路扫描结果汇合"
          active={variableReady}
        />
        <FlowArrow />
        <TheaterNode
          icon={<ShieldCheck size={18} />}
          label="Verify"
          title="交叉验证"
          detail="去重、排序、风险确认"
          state={verifyDone ? 'ready' : variableReady ? 'running' : 'pending'}
        />
        <FlowArrow />
        <VariableNode
          name="verifiedRisks"
          detail="验证后的风险集"
          active={verifyDone}
        />
        <FlowArrow />
        <TheaterNode
          icon={<FileText size={18} />}
          label="Report"
          title="最终报告"
          detail="修复计划与开放问题"
          state={reportDone ? 'ready' : verifyDone ? 'running' : 'pending'}
        />
      </div>
    </section>
  );
}

function TheaterNode({
  icon,
  label,
  title,
  detail,
  state,
}: {
  icon: ReactNode;
  label: string;
  title: string;
  detail: string;
  state: 'pending' | 'running' | 'ready';
}) {
  return (
    <div className={cn('theater-node', `theater-node--${state}`)}>
      <div className="theater-node__icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{title}</strong>
        <p>{detail}</p>
      </div>
    </div>
  );
}

function VariableNode({ name, detail, active }: { name: string; detail: string; active: boolean }) {
  return (
    <div className={cn('variable-node', active && 'variable-node--active')}>
      <Braces size={16} />
      <div>
        <strong>{name}</strong>
        <span>{detail}</span>
      </div>
    </div>
  );
}

function AgentOrbitCard({ name, label, status }: { name: string; label: string; status: string }) {
  return (
    <article className={cn('agent-orbit-card', `agent-orbit-card--${status}`)}>
      <div className="agent-orbit-card__status" />
      <span>{label}</span>
      <strong>{name}</strong>
      <em>{status}</em>
    </article>
  );
}

function FlowArrow() {
  return (
    <div className="flow-arrow" aria-hidden="true">
      <ChevronRight size={16} />
    </div>
  );
}

function WorkflowInspector({ run }: { run: WorkflowRunDetail }) {
  const [activeTab, setActiveTab] = useState<InspectorTab>('stage');
  const activeNode = findActiveNode(run);
  const tabs: Array<{ id: InspectorTab; label: string }> = [
    { id: 'stage', label: '阶段' },
    { id: 'variables', label: '变量' },
    { id: 'agents', label: 'Agent' },
    { id: 'events', label: '事件' },
    { id: 'script', label: '脚本' },
  ];

  return (
    <aside className="workflow-inspector">
      <div className="workflow-inspector__tabs">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={cn(activeTab === tab.id && 'active')}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="workflow-inspector__body">
        {activeTab === 'stage' && <StageInspector node={activeNode} run={run} />}
        {activeTab === 'variables' && <VariablesInspector events={run.events} />}
        {activeTab === 'agents' && <AgentsInspector run={run} />}
        {activeTab === 'events' && <EventsInspector events={run.events} />}
        {activeTab === 'script' && <ScriptInspector script={run.script} />}
      </div>
    </aside>
  );
}

function StageInspector({ node, run }: { node: WorkflowNode | null; run: WorkflowRunDetail }) {
  return (
    <div className="inspector-section">
      <div className="inspector-kicker">当前阶段</div>
      <h3>{node?.title || (run.status === 'draft' ? '等待批准' : '等待 Runtime')}</h3>
      <p>
        {node?.instruction ||
          (run.status === 'draft'
            ? 'Workflow 已经编译完成。批准后 Runtime 会派发 Agent task，并把结果汇合为变量。'
            : 'Runtime 正在根据 workflow script 推进阶段。')}
      </p>
      <div className="inspector-facts">
        <Fact label="状态" value={statusText(run.status)} />
        <Fact label="阶段数" value={`${run.nodes.length}`} />
        <Fact label="事件数" value={`${run.eventCount}`} />
      </div>
    </div>
  );
}

function VariablesInspector({ events }: { events: WorkflowEvent[] }) {
  const variables = events.filter((event) => event.type === 'VARIABLE_UPDATED');
  return (
    <div className="inspector-section">
      <div className="inspector-kicker">变量流</div>
      <h3>Runtime Variables</h3>
      <p>变量是 workflow script 的中间状态，不需要塞回主对话上下文。</p>
      <div className="variable-list">
        <VariableRow name="scanReports" status={variables.length > 0 ? 'ready' : 'waiting'} preview="backend / frontend / docs / tests 四路扫描摘要" />
        <VariableRow name="verifiedRisks" status={hasEvent(events, 'STAGE_COMPLETED', '交叉验证') ? 'ready' : 'waiting'} preview="去重、交叉验证后的风险列表" />
      </div>
    </div>
  );
}

function AgentsInspector({ run }: { run: WorkflowRunDetail }) {
  const items = run.script?.stages.find((stage) => stage.type === 'fanout')?.items || [];
  return (
    <div className="inspector-section">
      <div className="inspector-kicker">Agent Tasks</div>
      <h3>{items.length} 路并发</h3>
      <div className="agent-list">
        {items.map((item) => (
          <div key={item.key} className="agent-row">
            <strong>{item.agentName}</strong>
            <span>{item.instruction}</span>
            <Tag size="small" color={taskStatus(item.key, run.events) === 'completed' ? 'green' : 'blue'}>
              {taskStatus(item.key, run.events)}
            </Tag>
          </div>
        ))}
      </div>
    </div>
  );
}

function EventsInspector({ events }: { events: WorkflowEvent[] }) {
  return (
    <div className="inspector-section">
      <div className="inspector-kicker">事件</div>
      <h3>Workflow Events</h3>
      <div className="event-list">
        {events.slice(-12).map((event) => (
          <div key={event.id} className="event-row">
            <span>{formatTime(event.createdAt)}</span>
            <strong>{event.title || event.type}</strong>
            <p>{event.content}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function ScriptInspector({ script }: { script?: WorkflowScript }) {
  return (
    <div className="inspector-section">
      <div className="inspector-kicker">原始脚本</div>
      <h3>workflow.script.v1</h3>
      <pre className="workflow-script-json">{JSON.stringify(script || {}, null, 2)}</pre>
    </div>
  );
}

function AgentStream({ events }: { events: WorkflowEvent[] }) {
  const streamEvents = events.filter((event) => (
    event.type.includes('TASK') ||
    event.type.includes('VARIABLE') ||
    event.type.includes('BARRIER') ||
    event.type.includes('STAGE')
  ));

  return (
    <footer className="agent-stream">
      <div className="agent-stream__label">Agent Stream</div>
      <div className="agent-stream__items">
        {streamEvents.slice(-8).map((event) => (
          <span key={event.id}>
            <em>{formatTime(event.createdAt)}</em>
            {event.title || event.type}
          </span>
        ))}
        {streamEvents.length === 0 && <span>等待批准运行 workflow</span>}
      </div>
    </footer>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function VariableRow({ name, status, preview }: { name: string; status: string; preview: string }) {
  return (
    <div className={cn('variable-row', `variable-row--${status}`)}>
      <Braces size={15} />
      <div>
        <strong>{name}</strong>
        <span>{preview}</span>
      </div>
      <em>{status}</em>
    </div>
  );
}

function WorkflowStatusTag({ status }: { status: WorkflowRunStatus | string }) {
  const color = status === 'completed'
    ? 'green'
    : status === 'failed'
      ? 'red'
      : status === 'running'
        ? 'blue'
        : 'transparent';
  return <Tag size="small" color={color}>{statusText(status)}</Tag>;
}

function findActiveNode(run: WorkflowRunDetail | null): WorkflowNode | null {
  if (!run) return null;
  return (
    run.nodes.find((node) => node.id === run.currentNodeId) ||
    run.nodes.find((node) => node.status === 'running') ||
    run.nodes.find((node) => node.status !== 'completed') ||
    run.nodes[run.nodes.length - 1] ||
    null
  );
}

function taskStatus(key: string, events: WorkflowEvent[]) {
  const matching = events.filter((event) => {
    const task = event.payload?.task;
    return task && typeof task === 'object' && 'key' in task && task.key === key;
  });
  const last = matching[matching.length - 1];
  if (!last) return 'pending';
  return last.type === 'AGENT_TASK_COMPLETED' ? 'completed' : 'running';
}

function hasEvent(events: WorkflowEvent[], type: string, title?: string) {
  return events.some((event) => event.type === type && (!title || event.title === title));
}

function statusText(status: string) {
  const labels: Record<string, string> = {
    draft: '待批准',
    running: '运行中',
    waiting: '等待',
    failed: '失败',
    completed: '已完成',
    cancelled: '已取消',
  };
  return labels[status] || status;
}

function formatDateTime(value: string) {
  if (!value) return '';
  return new Date(value).toLocaleString();
}

function formatTime(value: string) {
  if (!value) return '';
  return new Date(value).toLocaleTimeString();
}

function formatNumber(value: number) {
  if (value >= 1000) return `${Math.round(value / 1000)}k`;
  return String(value);
}
