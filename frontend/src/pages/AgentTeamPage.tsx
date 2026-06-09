/**
 * AgentTeamPage — Agent Team 管理页面
 *
 * 双面板布局：
 * - 左侧：Agent 列表（卡片）+ 创建按钮
 * - 右侧：AgentEditor 表单（选中时显示）
 *
 * 功能：
 * - 创建/编辑/删除 Agent
 * - 查看 Agent 配置详情
 *
 * @see css-modern-layouts: Sidebar + Main
 */

import { useState, useCallback, useEffect } from 'react';
import { AlertCircle, Bot, CheckCircle2, Plus, Sparkles, Trash2, Users, WandSparkles } from 'lucide-react';
import { Button, Empty, Input, Modal, Select, Spinner, Tag } from '@agentscope-ai/design';
import { useAgentMemory } from '../hooks/useAgentMemory';
import { useAgentBuilder } from '../hooks/useAgentBuilder';
import { useAgents } from '../hooks/useAgents';
import { useTeams } from '../hooks/useTeams';
import { AgentCard } from '../features/agents/AgentCard';
import { AgentEditor } from '../features/agents/AgentEditor';
import { DEFAULT_MODEL_CONFIG, type AgentCreatePayload, type ModelsResponse } from '../types/agent';
import type { Agent } from '../types/agent';
import type { BuilderMode, BuilderPlan } from '../types/agentBuilder';
import type { TeamRecord } from '../types/team';

/**
 * AgentTeamPage 组件
 */
export function AgentTeamPage() {
  const { agents, loading, models, createAgent, updateAgent, deleteAgent, refresh: refreshAgents } = useAgents();
  const { teams, loading: teamsLoading, createTeam, deleteTeam, refresh: refreshTeams } = useTeams();
  const { planning, applying, error: builderError, createPlan, applyPlan } = useAgentBuilder();
  const [mode, setMode] = useState<'agents' | 'teams'>('agents');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [createTeamOpen, setCreateTeamOpen] = useState(false);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [builderRequirement, setBuilderRequirement] = useState('');
  const [builderMode, setBuilderMode] = useState<BuilderMode>('team');
  const [builderModel, setBuilderModel] = useState('');
  const [builderPlan, setBuilderPlan] = useState<BuilderPlan | null>(null);
  const [teamForm, setTeamForm] = useState({
    name: '',
    description: '',
    leaderAgentId: '',
    workerAgentIds: [] as string[],
  });

  // 获取选中的 Agent
  const selectedAgent = agents.find((a) => a.id === selectedId) || null;
  const selectedTeam = teams.find((team) => team.id === selectedTeamId) || null;

  /** 创建新 Agent */
  const handleCreate = useCallback(async (data: AgentCreatePayload) => {
    const newAgent = await createAgent(data);
    setSelectedId(newAgent.id);
    setCreating(false);
  }, [createAgent]);

  /** 更新 Agent */
  const handleUpdate = useCallback(async (data: AgentCreatePayload) => {
    if (!selectedId) return;
    await updateAgent(selectedId, data);
  }, [selectedId, updateAgent]);

  /** 删除 Agent */
  const handleDelete = useCallback(async (id: string) => {
    await deleteAgent(id);
    if (selectedId === id) {
      setSelectedId(null);
    }
    setDeleteConfirm(null);
  }, [selectedId, deleteAgent]);

  const handleCreateTeam = useCallback(async () => {
    if (!teamForm.name.trim() || !teamForm.leaderAgentId) return;
    const team = await createTeam({
      name: teamForm.name.trim(),
      description: teamForm.description.trim(),
      leaderAgentId: teamForm.leaderAgentId,
      workerAgentIds: teamForm.workerAgentIds,
    });
    setSelectedTeamId(team.id);
    setCreateTeamOpen(false);
    setTeamForm({
      name: '',
      description: '',
      leaderAgentId: '',
      workerAgentIds: [],
    });
  }, [createTeam, teamForm]);

  const handleCreateBuilderPlan = useCallback(async () => {
    if (!builderRequirement.trim()) return;
    const plan = await createPlan({
      requirement: builderRequirement.trim(),
      mode: builderMode,
      preferredModel: builderModel || undefined,
    });
    setBuilderPlan(plan);
  }, [builderMode, builderModel, builderRequirement, createPlan]);

  const handleApplyBuilderPlan = useCallback(async () => {
    if (!builderPlan) return;
    const result = await applyPlan(builderPlan);
    await Promise.all([refreshAgents(), refreshTeams()]);
    if (result.createdTeam) {
      setMode('teams');
      setSelectedTeamId(result.createdTeam.id);
      setSelectedId(null);
    } else if (result.createdAgents[0]) {
      setMode('agents');
      setSelectedId(result.createdAgents[0].id);
      setSelectedTeamId(null);
    }
    setBuilderOpen(false);
    setBuilderPlan(null);
    setBuilderRequirement('');
  }, [applyPlan, builderPlan, refreshAgents, refreshTeams]);

  return (
    <div className="grid h-full min-h-0 grid-cols-[280px_1fr] overflow-hidden">
      {/* 左侧列表 */}
      <div className="flex min-h-0 flex-col border-r border-[var(--color-border)]">
        {/* 列表头部 */}
        <div className="p-4 border-b border-[var(--color-border)]">
          <div className="mb-3 grid grid-cols-2 gap-2">
            <Button
              type={mode === 'agents' ? 'primary' : 'default'}
              onClick={() => setMode('agents')}
            >
              Agents
            </Button>
            <Button
              type={mode === 'teams' ? 'primary' : 'default'}
              onClick={() => setMode('teams')}
            >
              Teams
            </Button>
          </div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-base font-medium text-[var(--color-text-primary)]">
              {mode === 'agents' ? 'Agents' : 'Teams'}
            </h2>
            {mode === 'agents' ? (
              <div className="flex items-center gap-2">
                <Button
                  size="small"
                  icon={<WandSparkles size={14} />}
                  onClick={() => setBuilderOpen(true)}
                  aria-label="智能创建"
                />
                <Button
                  type="primary"
                  size="small"
                  icon={<Plus size={14} />}
                  onClick={() => {
                    setCreating(true);
                    setSelectedId(null);
                  }}
                  aria-label="创建 Agent"
                />
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <Button
                  size="small"
                  icon={<WandSparkles size={14} />}
                  onClick={() => setBuilderOpen(true)}
                  aria-label="智能创建"
                />
                <Button
                  type="primary"
                  size="small"
                  icon={<Plus size={14} />}
                  onClick={() => setCreateTeamOpen(true)}
                  aria-label="创建 Team"
                />
              </div>
            )}
          </div>
          <div className="text-xs text-[var(--color-text-secondary)]">
            {mode === 'agents' ? `${agents.length} 个 Agent` : `${teams.length} 个 Team`}
          </div>
        </div>

        {/* Agent 列表 */}
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {mode === 'agents' && loading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : mode === 'agents' && agents.length === 0 ? (
            <Empty type="noData" description="暂无 Agent" />
          ) : mode === 'agents' ? (
            agents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                selected={selectedId === agent.id}
                onSelect={() => {
                  setSelectedId(agent.id);
                  setCreating(false);
                }}
                onDelete={() => setDeleteConfirm(agent.id)}
              />
            ))
          ) : teamsLoading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : teams.length === 0 ? (
            <Empty type="noData" description="暂无 Team" />
          ) : (
            teams.map((team) => (
              <button
                key={team.id}
                type="button"
                onClick={() => setSelectedTeamId(team.id)}
                className={`w-full rounded-md px-3 py-2 text-left transition-all duration-300 ease-out ${
                  selectedTeamId === team.id
                    ? 'bg-[var(--color-bg-secondary)] ring-1 ring-[var(--color-accent)]'
                    : 'hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)] hover:shadow-sm'
                }`}
              >
                <div className="flex items-start gap-2">
                  <Users size={16} className="mt-0.5 text-[var(--color-text-secondary)]" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                      {team.name}
                    </div>
                    <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                      {team.members.length} 个成员
                    </div>
                  </div>
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* 右侧编辑器 */}
      <div className="h-full min-h-0 overflow-hidden">
        {creating ? (
          <AgentEditor
            agent={null}
            models={models}
            onSave={handleCreate}
            onCancel={() => setCreating(false)}
          />
        ) : mode === 'agents' && selectedAgent ? (
          <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_360px]">
            <div className="min-h-0 overflow-hidden">
              <AgentEditor
                agent={selectedAgent}
                models={models}
                onSave={handleUpdate}
                onCancel={() => setSelectedId(null)}
              />
            </div>
            <div className="min-h-0 border-l border-[var(--color-border)]">
              <AgentMemoryPanel agent={selectedAgent} />
            </div>
          </div>
        ) : mode === 'teams' && selectedTeam ? (
          <TeamDetailPanel
            team={selectedTeam}
            agents={agents}
            onDelete={async () => {
              await deleteTeam(selectedTeam.id);
              setSelectedTeamId(null);
            }}
          />
        ) : (
          <div className="h-full flex items-center justify-center text-[var(--color-text-tertiary)]">
            <div className="text-center">
              {mode === 'agents' ? (
                <Bot size={48} strokeWidth={1} className="mx-auto mb-3" />
              ) : (
                <Users size={48} strokeWidth={1} className="mx-auto mb-3" />
              )}
              <div className="text-sm">
                {mode === 'agents' ? '选择一个 Agent 开始编辑' : '选择一个 Team 查看成员'}
              </div>
              <div className="text-xs mt-1">
                {mode === 'agents' ? '或点击 + 创建新的 Agent' : '或点击 + 创建新的 Team'}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 删除确认对话框 */}
      <Modal
        title="确认删除"
        open={Boolean(deleteConfirm)}
        onCancel={() => setDeleteConfirm(null)}
        onOk={() => deleteConfirm && handleDelete(deleteConfirm)}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
      >
        确定要删除这个 Agent 吗？此操作不可撤销。
      </Modal>

      <Modal
        title="创建 Team"
        open={createTeamOpen}
        onCancel={() => setCreateTeamOpen(false)}
        onOk={handleCreateTeam}
        okText="创建"
        cancelText="取消"
      >
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">名称</label>
            <Input
              value={teamForm.name}
              onChange={(event) => setTeamForm({ ...teamForm, name: event.target.value })}
              placeholder="如：Research Team"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">描述</label>
            <Input
              value={teamForm.description}
              onChange={(event) => setTeamForm({ ...teamForm, description: event.target.value })}
              placeholder="Team 的目标或职责"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">Leader Agent</label>
            <Select
              value={teamForm.leaderAgentId}
              onChange={(value) => setTeamForm({ ...teamForm, leaderAgentId: value })}
              options={agents.map((agent) => ({ label: agent.name, value: agent.id }))}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">Worker Agents</label>
            <Select
              mode="multiple"
              value={teamForm.workerAgentIds}
              onChange={(value) => setTeamForm({ ...teamForm, workerAgentIds: value })}
              options={agents
                .filter((agent) => agent.id !== teamForm.leaderAgentId)
                .map((agent) => ({ label: agent.name, value: agent.id }))}
              style={{ width: '100%' }}
            />
          </div>
        </div>
      </Modal>

      <Modal
        title="智能编排 Agent / Team"
        open={builderOpen}
        onCancel={() => {
          setBuilderOpen(false);
          setBuilderPlan(null);
        }}
        footer={null}
        width={980}
      >
        <AgentBuilderWizard
          requirement={builderRequirement}
          mode={builderMode}
          model={builderModel}
          models={models}
          plan={builderPlan}
          planning={planning}
          applying={applying}
          error={builderError}
          onRequirementChange={setBuilderRequirement}
          onModeChange={setBuilderMode}
          onModelChange={setBuilderModel}
          onCreatePlan={handleCreateBuilderPlan}
          onApplyPlan={handleApplyBuilderPlan}
          onResetPlan={() => setBuilderPlan(null)}
        />
      </Modal>
    </div>
  );
}

function AgentBuilderWizard({
  requirement,
  mode,
  model,
  models,
  plan,
  planning,
  applying,
  error,
  onRequirementChange,
  onModeChange,
  onModelChange,
  onCreatePlan,
  onApplyPlan,
  onResetPlan,
}: {
  requirement: string;
  mode: BuilderMode;
  model: string;
  models: ModelsResponse | null;
  plan: BuilderPlan | null;
  planning: boolean;
  applying: boolean;
  error: string | null;
  onRequirementChange: (value: string) => void;
  onModeChange: (value: BuilderMode) => void;
  onModelChange: (value: string) => void;
  onCreatePlan: () => Promise<void>;
  onApplyPlan: () => Promise<void>;
  onResetPlan: () => void;
}) {
  const missingToolCount = plan?.agents.reduce((total, agent) => total + (agent.missingTools || []).length, 0) || 0;
  const missingSkillCount = plan?.agents.reduce((total, agent) => total + (agent.missingSkills || []).length, 0) || 0;

  return (
    <div
      className="grid min-h-0 grid-cols-[330px_1fr] overflow-hidden"
      style={{ height: 'min(72vh, 720px)' }}
    >
      <div className="flex min-h-0 flex-col overflow-y-auto border-r border-[var(--color-border)] pr-4">
        <div className="mb-4 flex items-center gap-2 text-sm font-medium text-[var(--color-text-primary)]">
          <Sparkles size={15} color="var(--color-accent)" />
          <span>从需求生成可审核草案</span>
        </div>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">创建模式</label>
            <div className="grid grid-cols-2 gap-2">
              <Button
                type={mode === 'team' ? 'primary' : 'default'}
                onClick={() => {
                  onModeChange('team');
                  onResetPlan();
                }}
              >
                Team
              </Button>
              <Button
                type={mode === 'agents' ? 'primary' : 'default'}
                onClick={() => {
                  onModeChange('agents');
                  onResetPlan();
                }}
              >
                Agents
              </Button>
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">规划模型</label>
            <Select
              value={model}
              onChange={(value) => onModelChange(value)}
              options={[
                { label: '使用系统默认模型', value: '' },
                ...(models?.primary || []).map((item) => ({
                  label: item.name,
                  value: item.id,
                })),
              ]}
              style={{ width: '100%' }}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">用户需求</label>
            <textarea
              value={requirement}
              onChange={(event) => {
                onRequirementChange(event.target.value);
                if (plan) onResetPlan();
              }}
              placeholder="例如：我需要一个能做产品需求分析、技术方案设计、代码实现和测试验证的团队。"
              className="h-52 w-full resize-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2 text-sm text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent)]"
            />
          </div>

          {error && (
            <div className="rounded-md bg-[var(--color-bg-secondary)] p-3 text-xs text-[var(--color-error)]">
              {error}
            </div>
          )}

          <Button
            type="primary"
            loading={planning}
            disabled={!requirement.trim() || applying}
            onClick={onCreatePlan}
            style={{ width: '100%' }}
          >
            <WandSparkles size={14} />
            生成草案
          </Button>
        </div>
      </div>

      <div className="h-full min-h-0 overflow-y-auto pl-4 pr-1">
        {!plan ? (
          <div className="flex h-full items-center justify-center text-[var(--color-text-tertiary)]">
            <div className="text-center">
              <WandSparkles size={44} strokeWidth={1.2} className="mx-auto mb-3" />
              <div className="text-sm">输入需求后生成 Agent / Team 草案</div>
              <div className="mt-1 text-xs">系统会匹配已启用的 MCP 工具和 Skills，并标出缺失能力</div>
            </div>
          </div>
        ) : (
          <div className="space-y-4 pb-2">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-base font-medium text-[var(--color-text-primary)]">
                  {plan.team?.name || 'Agent 创建草案'}
                </div>
                <div className="mt-1 text-sm text-[var(--color-text-secondary)]">
                  {plan.team?.description || plan.requirement}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <Tag size="small" color="transparent">{plan.mode}</Tag>
                <Tag size="small" color={missingToolCount + missingSkillCount > 0 ? 'orange' : 'green'}>
                  缺失 {missingToolCount + missingSkillCount}
                </Tag>
              </div>
            </div>

            {plan.warnings.length > 0 && (
              <div className="space-y-2 rounded-md bg-[var(--color-bg-secondary)] p-3">
                {plan.warnings.map((warning) => (
                  <div key={warning} className="flex items-start gap-2 text-xs text-[var(--color-warning)]">
                    <AlertCircle size={13} className="mt-0.5 shrink-0" />
                    <span>{warning}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="grid gap-3">
              {plan.agents.map((agent) => (
                <BuilderAgentDraftCard key={agent.tempId} agent={agent} mode={plan.mode} />
              ))}
            </div>

            <div className="flex justify-end gap-2 border-t border-[var(--color-border)] pt-4">
              <Button disabled={applying || planning} onClick={onResetPlan}>
                重新填写
              </Button>
              <Button type="primary" loading={applying} disabled={planning} onClick={onApplyPlan}>
                <CheckCircle2 size={14} />
                确认创建
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function BuilderAgentDraftCard({
  agent,
  mode,
}: {
  agent: BuilderPlan['agents'][number];
  mode: BuilderMode;
}) {
  const matchedTools = (agent.recommendedTools || []).filter((item) => item.matched);
  const matchedSkills = (agent.recommendedSkills || []).filter((item) => item.matched);
  const modelConfig = agent.modelConfig || DEFAULT_MODEL_CONFIG;

  return (
    <div className="rounded-md bg-[var(--color-bg-secondary)] p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Tag size="small" color={mode === 'agents' ? 'green' : agent.role === 'leader' ? 'blue' : 'mauve'}>
              {mode === 'agents' ? 'Agent' : agent.role === 'leader' ? 'Leader' : 'Worker'}
            </Tag>
            <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {agent.name}
            </div>
          </div>
          <div className="mt-2 text-xs text-[var(--color-text-secondary)]">
            {agent.description || '暂无描述'}
          </div>
        </div>
        <Tag size="small" color="transparent">
          {modelConfig.primary}
        </Tag>
      </div>

      <div className="mb-3 line-clamp-3 whitespace-pre-wrap rounded bg-[var(--color-bg-primary)] p-3 text-xs text-[var(--color-text-tertiary)]">
        {agent.systemPrompt}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <CapabilityBlock
          title="MCP 工具"
          matched={matchedTools.map((item) => item.name)}
          missing={agent.missingTools || []}
        />
        <CapabilityBlock
          title="Skills"
          matched={matchedSkills.map((item) => item.name)}
          missing={agent.missingSkills || []}
        />
      </div>
    </div>
  );
}

function CapabilityBlock({
  title,
  matched,
  missing,
}: {
  title: string;
  matched: string[];
  missing: string[];
}) {
  return (
    <div>
      <div className="mb-2 text-xs text-[var(--color-text-tertiary)]">{title}</div>
      <div className="flex flex-wrap gap-1">
        {matched.length === 0 && missing.length === 0 && (
          <Tag size="small" color="transparent">未绑定</Tag>
        )}
        {matched.map((name) => (
          <Tag key={`matched-${name}`} size="small" color="green">
            {name}
          </Tag>
        ))}
        {missing.map((name) => (
          <Tag key={`missing-${name}`} size="small" color="orange">
            缺失 {name}
          </Tag>
        ))}
      </div>
    </div>
  );
}

function AgentMemoryPanel({ agent }: { agent: Agent }) {
  const { memory, loading, error, refresh } = useAgentMemory(agent.id);
  const [mainDraft, setMainDraft] = useState('');
  const [savingMain, setSavingMain] = useState(false);
  const [busyCandidateId, setBusyCandidateId] = useState<string | null>(null);

  useEffect(() => {
    setMainDraft(memory?.main_memory || '');
  }, [memory?.main_memory]);

  const saveMainMemory = async () => {
    setSavingMain(true);
    try {
      const response = await fetch(`/api/agent-memory/${agent.id}/main`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: mainDraft }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await refresh();
    } finally {
      setSavingMain(false);
    }
  };

  const resolveCandidate = async (candidateId: string, action: 'approve' | 'reject') => {
    setBusyCandidateId(candidateId);
    try {
      const response = await fetch(`/api/agent-memory/${agent.id}/candidates/${candidateId}/${action}`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await refresh();
    } finally {
      setBusyCandidateId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  if (error) {
    return <div className="p-4 text-sm text-[var(--color-error)]">{error}</div>;
  }

  return (
    <div className="h-full overflow-y-auto p-4">
      {memory?.main_memory_path && (
        <div className="mb-4 rounded-md bg-[var(--color-bg-secondary)] p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium text-[var(--color-text-primary)]">主记忆 MEMORY.md</div>
              <div className="mt-1 break-all text-xs text-[var(--color-text-tertiary)]">
                {memory.main_memory_path}
              </div>
            </div>
            <Button size="small" type="primary" loading={savingMain} onClick={saveMainMemory}>
              保存
            </Button>
          </div>
          <Input.TextArea
            value={mainDraft}
            onChange={(event) => setMainDraft(event.target.value)}
            rows={10}
            className="mt-3"
            style={{ fontFamily: 'var(--font-mono)' }}
          />
        </div>
      )}

      <div className="mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">候选记忆</h3>
        <div className="mt-2 space-y-2">
          {memory?.candidates.length ? memory.candidates.map((candidate) => (
            <div key={candidate.id} className="rounded-md bg-[var(--color-bg-secondary)] p-3">
              <div className="text-sm font-medium text-[var(--color-text-primary)]">{candidate.title}</div>
              <div className="mt-1 whitespace-pre-wrap text-xs text-[var(--color-text-secondary)]">
                {candidate.content}
              </div>
              {candidate.tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {candidate.tags.map((tag) => (
                    <Tag key={tag} size="small" color="transparent">{tag}</Tag>
                  ))}
                </div>
              )}
              <div className="mt-3 flex justify-end gap-2">
                <Button
                  size="small"
                  loading={busyCandidateId === candidate.id}
                  onClick={() => resolveCandidate(candidate.id, 'reject')}
                >
                  忽略
                </Button>
                <Button
                  size="small"
                  type="primary"
                  loading={busyCandidateId === candidate.id}
                  onClick={() => resolveCandidate(candidate.id, 'approve')}
                >
                  写入主记忆
                </Button>
              </div>
            </div>
          )) : (
            <Empty type="noData" description="暂无候选记忆" />
          )}
        </div>
      </div>

      <div className="mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">已确认事实</h3>
        <div className="mt-2 space-y-2">
          {memory?.facts.length ? memory.facts.map((fact) => (
            <div key={fact.id} className="rounded-md bg-[var(--color-bg-secondary)] p-3">
              <div className="text-sm font-medium text-[var(--color-text-primary)]">{fact.title}</div>
              <div className="mt-1 text-xs text-[var(--color-text-secondary)]">{fact.content}</div>
            </div>
          )) : (
            <Empty type="noData" description="暂无已确认事实" />
          )}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">运行历史</h3>
        <div className="mt-2 space-y-2">
          {memory?.events.length ? memory.events.map((event) => (
            <div key={event.id} className="rounded-md bg-[var(--color-bg-secondary)] p-3">
              <div className="flex items-center gap-2">
                <Tag size="small" color="transparent">{event.type}</Tag>
                <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                  {event.title}
                </div>
              </div>
              <div className="mt-2 line-clamp-3 text-xs text-[var(--color-text-secondary)]">
                {event.content}
              </div>
            </div>
          )) : (
            <Empty type="noData" description="暂无运行历史" />
          )}
        </div>
      </div>
    </div>
  );
}

function TeamDetailPanel({
  team,
  agents,
  onDelete,
}: {
  team: TeamRecord;
  agents: Agent[];
  onDelete: () => Promise<void>;
}) {
  const agentNameById = new Map(agents.map((agent) => [agent.id, agent.name]));

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-medium text-[var(--color-text-primary)]">{team.name}</h2>
          <div className="mt-1 text-sm text-[var(--color-text-secondary)]">
            {team.description || '暂无描述'}
          </div>
        </div>
        <Button danger onClick={onDelete}>
          <Trash2 size={14} />
          删除
        </Button>
      </div>

      <div className="grid gap-3">
        {team.members.map((member) => (
          <div key={member.id} className="rounded-md bg-[var(--color-bg-secondary)] p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Tag size="small" color={member.role === 'leader' ? 'blue' : 'mauve'}>
                    {member.role}
                  </Tag>
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {agentNameById.get(member.agentId) || member.name}
                  </span>
                </div>
                <div className="mt-2 text-xs text-[var(--color-text-secondary)]">
                  {member.description}
                </div>
              </div>
              <Tag size="small" color="transparent">{member.status}</Tag>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
