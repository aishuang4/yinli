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

import { useState, useCallback, useEffect, useRef } from 'react';
import { AlertCircle, Archive, Bot, CheckCircle2, Download, Pencil, Pin, PinOff, Plus, RotateCcw, Save, Sparkles, Trash2, Upload, UserPlus, Users, WandSparkles, X } from 'lucide-react';
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
import type { AgentMemory, MemoryFact } from '../types/memory';
import type { TeamMember, TeamRecord } from '../types/team';
import { createId } from '../utils/id';

interface TeamTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  builtin: boolean;
  members: Array<{
    name: string;
    role: 'leader' | 'worker';
    agentId: string;
    keywords: string[];
    executionMode: 'parallel' | 'depends_on';
    dependsOn: string[];
    handoffPrompt: string;
  }>;
  createdAt: string;
  updatedAt: string;
}

/**
 * AgentTeamPage 组件
 */
export function AgentTeamPage() {
  const { agents, loading, models, createAgent, updateAgent, deleteAgent, refresh: refreshAgents } = useAgents();
  const { teams, loading: teamsLoading, createTeam, updateTeam, deleteTeam, refresh: refreshTeams } = useTeams();
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
  const importAgentInputRef = useRef<HTMLInputElement | null>(null);
  const importTeamInputRef = useRef<HTMLInputElement | null>(null);
  const [teamTemplates, setTeamTemplates] = useState<TeamTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState('');
  const [templateTeamName, setTemplateTeamName] = useState('');

  // 获取选中的 Agent
  const selectedAgent = agents.find((a) => a.id === selectedId) || null;
  const selectedTeam = teams.find((team) => team.id === selectedTeamId) || null;

  const refreshTeamTemplates = useCallback(async () => {
    const response = await fetch('/api/teams/templates');
    if (!response.ok) return;
    setTeamTemplates(await response.json());
  }, []);

  useEffect(() => {
    void refreshTeamTemplates();
  }, [refreshTeamTemplates]);

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

  const handleExportAgent = useCallback(async () => {
    if (!selectedAgent) return;
    const response = await fetch(`/api/agents/${selectedAgent.id}/export`);
    if (!response.ok) return;
    const data = await response.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${selectedAgent.name || 'agent'}-${selectedAgent.id}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }, [selectedAgent]);

  const handleImportAgentFile = useCallback(async (file: File) => {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const response = await fetch('/api/agents/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        package: parsed,
        conflictStrategy: 'new_id',
      }),
    });
    if (!response.ok) return;
    const result = await response.json();
    await refreshAgents();
    if (result.agent?.id) {
      setMode('agents');
      setSelectedId(result.agent.id);
      setSelectedTeamId(null);
    }
  }, [refreshAgents]);

  const handleExportTeam = useCallback(async () => {
    if (!selectedTeam) return;
    const response = await fetch(`/api/teams/${selectedTeam.id}/export`);
    if (!response.ok) return;
    const data = await response.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${selectedTeam.name || 'team'}-${selectedTeam.id}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }, [selectedTeam]);

  const handleImportTeamFile = useCallback(async (file: File) => {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const response = await fetch('/api/teams/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        package: parsed,
        conflictStrategy: 'new_id',
      }),
    });
    if (!response.ok) return;
    const result = await response.json();
    await refreshTeams();
    if (result.team?.id) {
      setMode('teams');
      setSelectedTeamId(result.team.id);
      setSelectedId(null);
    }
  }, [refreshTeams]);

  const handleCreateTeamFromTemplate = useCallback(async () => {
    if (!selectedTemplateId) return;
    const response = await fetch(`/api/teams/templates/${selectedTemplateId}/instantiate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: templateTeamName || undefined }),
    });
    if (!response.ok) return;
    const result = await response.json();
    await refreshTeams();
    if (result.team?.id) {
      setMode('teams');
      setSelectedTeamId(result.team.id);
      setSelectedId(null);
      setTemplateTeamName('');
    }
  }, [refreshTeams, selectedTemplateId, templateTeamName]);

  const handleSaveSelectedTeamAsTemplate = useCallback(async () => {
    if (!selectedTeam) return;
    const response = await fetch(`/api/teams/templates/from-team/${selectedTeam.id}`, {
      method: 'POST',
    });
    if (response.ok) {
      await refreshTeamTemplates();
    }
  }, [refreshTeamTemplates, selectedTeam]);

  return (
    <div className="grid h-full min-h-0 grid-cols-[280px_1fr] gap-4 overflow-hidden p-4">
      {/* 左侧列表 */}
      <div className="flex min-h-0 flex-col rounded-md bg-[var(--color-bg-primary)] shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
        {/* 列表头部 */}
        <div className="p-4">
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

          {mode === 'agents' && (
            <div className="mb-3 grid grid-cols-2 gap-2">
              <Button size="small" disabled={!selectedAgent} onClick={() => void handleExportAgent()}>
                <Download size={13} />
                导出
              </Button>
              <Button size="small" onClick={() => importAgentInputRef.current?.click()}>
                <Upload size={13} />
                导入
              </Button>
              <input
                ref={importAgentInputRef}
                type="file"
                accept="application/json,.json"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = '';
                  if (file) void handleImportAgentFile(file);
                }}
              />
            </div>
          )}

          {mode === 'teams' && (
            <div className="mb-3 space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <Button size="small" disabled={!selectedTeam} onClick={() => void handleExportTeam()}>
                  <Download size={13} />
                  导出
                </Button>
                <Button size="small" onClick={() => importTeamInputRef.current?.click()}>
                  <Upload size={13} />
                  导入
                </Button>
                <input
                  ref={importTeamInputRef}
                  type="file"
                  accept="application/json,.json"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = '';
                    if (file) void handleImportTeamFile(file);
                  }}
                />
              </div>
              <Select
                value={selectedTemplateId || undefined}
                onChange={(value) => setSelectedTemplateId(String(value))}
                options={teamTemplates.map((template) => ({
                  label: `${template.name}${template.builtin ? ' · 内置' : ''}`,
                  value: template.id,
                }))}
                style={{ width: '100%' }}
                placeholder="选择 Team 模板"
              />
              <input
                value={templateTeamName}
                onChange={(event) => setTemplateTeamName(event.target.value)}
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent)]"
                placeholder="新 Team 名称（可选）"
              />
              <div className="grid grid-cols-2 gap-2">
                <Button size="small" disabled={!selectedTemplateId} onClick={() => void handleCreateTeamFromTemplate()}>
                  <Plus size={13} />
                  用模板创建
                </Button>
                <Button size="small" disabled={!selectedTeam} onClick={() => void handleSaveSelectedTeamAsTemplate()}>
                  <Sparkles size={13} />
                  存为模板
                </Button>
              </div>
            </div>
          )}
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
                className={`w-full rounded-md border px-3 py-2 text-left transition-all duration-300 ease-out ${
                  selectedTeamId === team.id
                    ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg)] shadow-sm'
                    : 'border-[var(--color-border)] bg-[var(--color-bg-primary)] hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)] hover:shadow-md'
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
      <div className="h-full min-h-0 overflow-hidden rounded-md bg-[var(--color-bg-primary)] shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
        {creating ? (
          <AgentEditor
            agent={null}
            models={models}
            availableAgents={agents}
            onSave={handleCreate}
            onCancel={() => setCreating(false)}
          />
        ) : mode === 'agents' && selectedAgent ? (
          <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_360px] gap-4 p-4">
            <div className="min-h-0 overflow-hidden">
              <AgentEditor
                agent={selectedAgent}
                models={models}
                availableAgents={agents}
                onSave={handleUpdate}
                onCancel={() => setSelectedId(null)}
              />
            </div>
            <div className="min-h-0 rounded-md bg-[var(--color-bg-secondary)] shadow-sm">
              <AgentMemoryPanel agent={selectedAgent} />
            </div>
          </div>
        ) : mode === 'teams' && selectedTeam ? (
          <TeamDetailPanel
            team={selectedTeam}
            agents={agents}
            onUpdate={async (members) => {
              await updateTeam(selectedTeam.id, { members });
            }}
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
      className="grid min-h-0 grid-cols-[330px_1fr] gap-4 overflow-hidden"
      style={{ height: 'min(72vh, 720px)' }}
    >
      <div className="flex min-h-0 flex-col overflow-y-auto rounded-md bg-[var(--color-bg-secondary)] p-4 shadow-sm">
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
              className="h-52 w-full resize-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2 text-sm text-[var(--color-text-primary)] outline-none transition-colors duration-300 focus:border-[var(--color-accent)]"
            />
          </div>

          {error && (
            <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3 text-xs text-[var(--color-error)]">
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

      <div className="h-full min-h-0 overflow-y-auto rounded-md bg-[var(--color-bg-secondary)] p-4 shadow-sm">
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
              <div className="space-y-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
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

            <div className="flex justify-end gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3 shadow-sm">
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
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
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

      <div className="mb-3 line-clamp-3 whitespace-pre-wrap rounded border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3 text-xs text-[var(--color-text-tertiary)]">
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
  const { memory, loading, error, updateMemory } = useAgentMemory(agent.id);
  const [mainDraft, setMainDraft] = useState('');
  const [savingMain, setSavingMain] = useState(false);
  const [busyCandidateId, setBusyCandidateId] = useState<string | null>(null);
  const [mergingCandidates, setMergingCandidates] = useState(false);
  const [busyCleanupId, setBusyCleanupId] = useState<string | null>(null);
  const [busyFactId, setBusyFactId] = useState<string | null>(null);
  const [editingFactId, setEditingFactId] = useState<string | null>(null);
  const [factDraft, setFactDraft] = useState({ title: '', content: '', tags: '' });
  const [rejectCandidateId, setRejectCandidateId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [deleteFactId, setDeleteFactId] = useState<string | null>(null);
  const [cleanupConfirm, setCleanupConfirm] = useState<{ suggestionId: string; keepFactId?: string | null } | null>(null);

  useEffect(() => {
    setMainDraft(memory?.main_memory || '');
  }, [memory?.main_memory]);

  const applyReturnedMemory = async (response: Response) => {
    const nextMemory: AgentMemory = await response.json();
    updateMemory(() => nextMemory);
  };

  const saveMainMemory = async () => {
    setSavingMain(true);
    try {
      const response = await fetch(`/api/agent-memory/${agent.id}/main`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: mainDraft }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await applyReturnedMemory(response);
    } finally {
      setSavingMain(false);
    }
  };

  const resolveCandidate = async (candidateId: string, action: 'approve' | 'reject', reason = '') => {
    setBusyCandidateId(candidateId);
    try {
      const response = await fetch(`/api/agent-memory/${agent.id}/candidates/${candidateId}/${action}`, {
        method: 'POST',
        headers: action === 'reject' ? { 'Content-Type': 'application/json' } : undefined,
        body: action === 'reject' ? JSON.stringify({ reason }) : undefined,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (action === 'reject') {
        updateMemory((current) => ({
          ...current,
          candidates: current.candidates.filter((candidate) => candidate.id !== candidateId),
        }));
      } else {
        await applyReturnedMemory(response);
      }
    } finally {
      setBusyCandidateId(null);
      if (action === 'reject') {
        setRejectCandidateId(null);
        setRejectReason('');
      }
    }
  };

  const mergeDuplicateCandidates = async () => {
    setMergingCandidates(true);
    try {
      const response = await fetch(`/api/agent-memory/${agent.id}/candidates/merge-duplicates`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await applyReturnedMemory(response);
    } finally {
      setMergingCandidates(false);
    }
  };

  const startEditFact = (fact: MemoryFact) => {
    setEditingFactId(fact.id);
    setFactDraft({
      title: fact.title,
      content: fact.content,
      tags: fact.tags.join(', '),
    });
  };

  const patchFact = async (factId: string, patch: Partial<MemoryFact>) => {
    setBusyFactId(factId);
    try {
      const response = await fetch(`/api/agent-memory/${agent.id}/facts/${factId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const updatedFact: MemoryFact = await response.json();
      updateMemory((current) => ({
        ...current,
        facts: current.facts.map((fact) => (fact.id === factId ? updatedFact : fact)),
        cleanup_suggestions: current.cleanup_suggestions.filter((suggestion) => (
          !suggestion.fact_ids.includes(factId)
        )),
      }));
    } finally {
      setBusyFactId(null);
    }
  };

  const saveFactEdit = async (factId: string) => {
    const tags = factDraft.tags
      .split(',')
      .map((tag) => tag.trim())
      .filter(Boolean)
      .slice(0, 8);
    await patchFact(factId, {
      title: factDraft.title.trim(),
      content: factDraft.content.trim(),
      tags,
    });
    setEditingFactId(null);
  };

  const deleteFact = async (factId: string) => {
    setBusyFactId(factId);
    try {
      const response = await fetch(`/api/agent-memory/${agent.id}/facts/${factId}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      updateMemory((current) => ({
        ...current,
        facts: current.facts.filter((fact) => fact.id !== factId),
        cleanup_suggestions: current.cleanup_suggestions.filter((suggestion) => (
          !suggestion.fact_ids.includes(factId) && suggestion.keep_fact_id !== factId
        )),
      }));
    } finally {
      setBusyFactId(null);
      setDeleteFactId(null);
    }
  };

  const applyCleanupSuggestion = async (suggestionId: string, keepFactId?: string | null) => {
    setBusyCleanupId(suggestionId);
    try {
      const response = await fetch(`/api/agent-memory/${agent.id}/cleanup/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ suggestionId, keepFactId }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await applyReturnedMemory(response);
    } finally {
      setBusyCleanupId(null);
      setCleanupConfirm(null);
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
        <div className="mb-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
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
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium text-[var(--color-text-primary)]">候选记忆</h3>
          <Button
            size="small"
            loading={mergingCandidates}
            disabled={!memory?.candidates.length}
            onClick={() => void mergeDuplicateCandidates()}
          >
            合并重复
          </Button>
        </div>
        <div className="mt-2 space-y-2">
          {memory?.candidates.length ? memory.candidates.map((candidate) => (
            <div key={candidate.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 text-sm font-medium text-[var(--color-text-primary)]">
                  {candidate.title}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Tag size="small" color="transparent">{candidate.category || 'general'}</Tag>
                  <Tag size="small" color={(candidate.confidence || 0) >= 0.7 ? 'green' : 'orange'}>
                    {Math.round((candidate.confidence || 0) * 100)}%
                  </Tag>
                </div>
              </div>
              <div className="mt-1 whitespace-pre-wrap text-xs text-[var(--color-text-secondary)]">
                {candidate.content}
              </div>
              {candidate.source_session_id && (
                <div className="mt-2 truncate text-[11px] text-[var(--color-text-tertiary)]">
                  来源会话：{candidate.source_session_id}
                </div>
              )}
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
                  onClick={() => {
                    setRejectCandidateId(candidate.id);
                    setRejectReason('');
                  }}
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
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">清理建议</h3>
        <div className="mt-2 space-y-2">
          {memory?.cleanup_suggestions.length ? memory.cleanup_suggestions.map((suggestion) => (
            <div key={suggestion.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Tag size="small" color={suggestion.type === 'duplicate' ? 'blue' : suggestion.type === 'conflict' ? 'orange' : 'transparent'}>
                      {suggestion.type === 'duplicate' ? '重复' : suggestion.type === 'conflict' ? '疑似冲突' : '长期未更新'}
                    </Tag>
                    <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                      {suggestion.title}
                    </div>
                  </div>
                  <div className="mt-2 text-xs text-[var(--color-text-secondary)]">
                    {suggestion.description}
                  </div>
                  <div className="mt-2 text-[11px] text-[var(--color-text-tertiary)]">
                    涉及 {suggestion.fact_ids.length} 条记忆 · 置信度 {Math.round(suggestion.confidence * 100)}%
                  </div>
                </div>
                <Button
                  size="small"
                  loading={busyCleanupId === suggestion.id}
                  onClick={() => setCleanupConfirm({ suggestionId: suggestion.id, keepFactId: suggestion.keep_fact_id })}
                >
                  确认清理
                </Button>
              </div>
            </div>
          )) : (
            <Empty type="noData" description="暂无清理建议" />
          )}
        </div>
      </div>

      <div className="mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">已确认事实</h3>
        <div className="mt-2 space-y-2">
          {memory?.facts.length ? memory.facts.map((fact) => (
            <div key={fact.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
              {editingFactId === fact.id ? (
                <div className="space-y-2">
                  <Input
                    value={factDraft.title}
                    onChange={(event) => setFactDraft((current) => ({ ...current, title: event.target.value }))}
                    placeholder="记忆标题"
                  />
                  <Input.TextArea
                    value={factDraft.content}
                    onChange={(event) => setFactDraft((current) => ({ ...current, content: event.target.value }))}
                    rows={4}
                    placeholder="记忆内容"
                  />
                  <Input
                    value={factDraft.tags}
                    onChange={(event) => setFactDraft((current) => ({ ...current, tags: event.target.value }))}
                    placeholder="标签，用英文逗号分隔"
                  />
                  <div className="flex justify-end gap-2">
                    <Button size="small" icon={<X size={13} />} onClick={() => setEditingFactId(null)}>
                      取消
                    </Button>
                    <Button
                      size="small"
                      type="primary"
                      icon={<Save size={13} />}
                      loading={busyFactId === fact.id}
                      disabled={!factDraft.title.trim() || !factDraft.content.trim()}
                      onClick={() => void saveFactEdit(fact.id)}
                    >
                      保存
                    </Button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex min-w-0 items-center gap-2">
                        {fact.pinned && <Pin size={13} className="shrink-0 text-[var(--color-primary)]" />}
                        <div className={`truncate text-sm font-medium ${fact.status === 'outdated' ? 'text-[var(--color-text-tertiary)] line-through' : 'text-[var(--color-text-primary)]'}`}>
                          {fact.title}
                        </div>
                      </div>
                      <div className="mt-1 whitespace-pre-wrap break-words text-xs text-[var(--color-text-secondary)]">
                        {fact.content}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        size="small"
                        icon={fact.pinned ? <PinOff size={13} /> : <Pin size={13} />}
                        loading={busyFactId === fact.id}
                        onClick={() => void patchFact(fact.id, { pinned: !fact.pinned })}
                        aria-label={fact.pinned ? '取消置顶' : '置顶'}
                      />
                      <Button
                        size="small"
                        icon={<Pencil size={13} />}
                        onClick={() => startEditFact(fact)}
                        aria-label="编辑"
                      />
                      <Button
                        size="small"
                        icon={fact.status === 'outdated' ? <RotateCcw size={13} /> : <Archive size={13} />}
                        loading={busyFactId === fact.id}
                        onClick={() => void patchFact(fact.id, { status: fact.status === 'outdated' ? 'active' : 'outdated' })}
                        aria-label={fact.status === 'outdated' ? '恢复有效' : '标记过期'}
                      />
                      <Button
                        size="small"
                        danger
                        icon={<Trash2 size={13} />}
                        loading={busyFactId === fact.id}
                        onClick={() => setDeleteFactId(fact.id)}
                        aria-label="删除"
                      />
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-1">
                    <Tag size="small" color={fact.status === 'active' ? 'green' : 'orange'}>
                      {fact.status === 'active' ? '有效' : '已过期'}
                    </Tag>
                    <Tag size="small" color="transparent">
                      注入 {fact.access_count || 0} 次
                    </Tag>
                    {fact.tags.map((tag) => (
                      <Tag key={tag} size="small" color="transparent">{tag}</Tag>
                    ))}
                  </div>
                  {fact.last_accessed_at && (
                    <div className="mt-2 text-[11px] text-[var(--color-text-tertiary)]">
                      最后注入：{new Date(fact.last_accessed_at).toLocaleString()}
                    </div>
                  )}
                </>
              )}
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
            <div key={event.id} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
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

      <Modal
        title="忽略候选记忆"
        open={Boolean(rejectCandidateId)}
        onCancel={() => {
          setRejectCandidateId(null);
          setRejectReason('');
        }}
        onOk={() => rejectCandidateId && void resolveCandidate(rejectCandidateId, 'reject', rejectReason)}
        okText="确认忽略"
        cancelText="取消"
        confirmLoading={Boolean(rejectCandidateId && busyCandidateId === rejectCandidateId)}
        width={420}
      >
        <div className="space-y-3">
          <div className="text-sm text-[var(--color-text-secondary)]">
            可填写忽略原因，便于后续审查记忆候选。
          </div>
          <Input.TextArea
            value={rejectReason}
            onChange={(event) => setRejectReason(event.target.value)}
            rows={4}
            placeholder="忽略原因（可选）"
          />
        </div>
      </Modal>

      <Modal
        title="删除记忆事实"
        open={Boolean(deleteFactId)}
        onCancel={() => setDeleteFactId(null)}
        onOk={() => deleteFactId && void deleteFact(deleteFactId)}
        okText="确认删除"
        cancelText="取消"
        confirmLoading={Boolean(deleteFactId && busyFactId === deleteFactId)}
        width={420}
      >
        <div className="text-sm text-[var(--color-text-secondary)]">
          删除后这条已确认事实会从 Agent 记忆中移除。这个操作不会影响主记忆文件中的其他内容。
        </div>
      </Modal>

      <Modal
        title="执行记忆清理"
        open={Boolean(cleanupConfirm)}
        onCancel={() => setCleanupConfirm(null)}
        onOk={() => cleanupConfirm && void applyCleanupSuggestion(cleanupConfirm.suggestionId, cleanupConfirm.keepFactId)}
        okText="确认清理"
        cancelText="取消"
        confirmLoading={Boolean(cleanupConfirm && busyCleanupId === cleanupConfirm.suggestionId)}
        width={460}
      >
        <div className="text-sm text-[var(--color-text-secondary)]">
          相关记忆会被标记为过期，不会被删除。过期记忆仍可在面板中查看，但不会再注入 Agent 运行上下文。
        </div>
      </Modal>
    </div>
  );
}

function TeamDetailPanel({
  team,
  agents,
  onUpdate,
  onDelete,
}: {
  team: TeamRecord;
  agents: Agent[];
  onUpdate: (members: TeamMember[]) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const agentNameById = new Map(agents.map((agent) => [agent.id, agent.name]));
  const [memberDrafts, setMemberDrafts] = useState<TeamMember[]>(team.members);
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(team.members[0]?.id || null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setMemberDrafts(team.members);
    setSelectedMemberId((current) => (
      current && team.members.some((member) => member.id === current)
        ? current
        : team.members[0]?.id || null
    ));
  }, [team.id, team.members]);

  const workerMembers = memberDrafts.filter((member) => member.role === 'worker');
  const selectedMember = memberDrafts.find((member) => member.id === selectedMemberId) || null;
  const existingAgentIds = new Set(memberDrafts.map((member) => member.agentId));
  const addableAgentOptions = agents
    .filter((agent) => !existingAgentIds.has(agent.id))
    .map((agent) => ({ label: agent.name, value: agent.id }));

  const updateMember = (memberId: string, patch: Partial<TeamMember>) => {
    setMemberDrafts((current) => current.map((member) => (
      member.id === memberId
        ? {
            ...member,
            ...patch,
            dependsOn: patch.executionMode === 'parallel' ? [] : patch.dependsOn ?? member.dependsOn,
          }
        : member
    )));
  };

  const addWorker = (agentId: string) => {
    const agent = agents.find((item) => item.id === agentId);
    if (!agent) return;

    const nextMember: TeamMember = {
      id: createId('member'),
      agentId: agent.id,
      name: agent.name,
      role: 'worker',
      description: agent.description || 'Team worker session',
      executionMode: 'parallel',
      dependsOn: [],
      handoffPrompt: '',
      status: 'idle',
    };

    setMemberDrafts((current) => [...current, nextMember]);
    setSelectedMemberId(nextMember.id);
  };

  const removeWorker = (memberId: string) => {
    const removed = memberDrafts.find((member) => member.id === memberId);
    if (!removed || removed.role === 'leader') return;

    setMemberDrafts((current) => current
      .filter((member) => member.id !== memberId)
      .map((member) => ({
        ...member,
        dependsOn: (member.dependsOn || []).filter((dependency) => dependency !== removed.agentId && dependency !== removed.id),
      })));
    setSelectedMemberId((current) => (
      current === memberId
        ? memberDrafts.find((member) => member.id !== memberId)?.id || null
        : current
    ));
  };

  const saveTeamMembers = async () => {
    setSaving(true);
    try {
      await onUpdate(memberDrafts);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_380px] gap-4 overflow-hidden p-4">
      <div className="min-h-0 overflow-y-auto rounded-md bg-[var(--color-bg-primary)] p-5 shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-medium text-[var(--color-text-primary)]">{team.name}</h2>
          <div className="mt-1 text-sm text-[var(--color-text-secondary)]">
            {team.description || '暂无描述'}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button type="primary" loading={saving} onClick={saveTeamMembers}>
            保存调度
          </Button>
          <Button danger icon={<Trash2 size={14} />} onClick={onDelete}>
            删除
          </Button>
        </div>
      </div>

      <div className="mb-4 rounded-md bg-[var(--color-bg-secondary)] p-3 shadow-sm">
        <label className="mb-2 flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
          <UserPlus size={13} />
          添加 Worker
        </label>
        <Select
          key={memberDrafts.length}
          value={undefined}
          onChange={(value) => addWorker(String(value))}
          options={addableAgentOptions}
          placeholder={addableAgentOptions.length ? '选择一个 Agent 加入 Team' : '没有可添加的 Agent'}
          disabled={addableAgentOptions.length === 0}
          style={{ width: '100%' }}
        />
      </div>

      <div className="grid gap-3">
        {memberDrafts.map((member) => {
          const selected = member.id === selectedMemberId;

          return (
          <button
            key={member.id}
            type="button"
            onClick={() => setSelectedMemberId(member.id)}
            className={[
              'ui-card-transition w-full rounded-md border p-4 text-left',
              selected
                ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg)] shadow-sm'
                : 'border-[var(--color-border)] bg-[var(--color-bg-primary)] hover:-translate-y-0.5 hover:bg-[var(--color-bg-hover)] hover:shadow-md',
            ].join(' ')}
          >
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
              <div className="flex shrink-0 items-center gap-2">
                <Tag size="small" color="transparent">{member.status}</Tag>
                {member.role === 'worker' && (
                  <Button
                    size="small"
                    danger
                    icon={<Trash2 size={13} />}
                    onClick={(event) => {
                      event.stopPropagation();
                      removeWorker(member.id);
                    }}
                    aria-label="移除成员"
                  />
                )}
              </div>
            </div>
          </button>
          );
        })}
      </div>
      </div>

      <div className="min-h-0 rounded-md bg-[var(--color-bg-primary)] shadow-[0_12px_30px_rgba(0,0,0,0.08)] transition-all duration-300">
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex items-center justify-between p-4">
            <div>
              <h3 className="text-sm font-medium text-[var(--color-text-primary)]">成员设置</h3>
              <div className="mt-1 text-xs text-[var(--color-text-secondary)]">
                点击左侧成员后编辑本轮协作参数
              </div>
            </div>
            {selectedMember && (
              <Button
                size="small"
                icon={<X size={14} />}
                onClick={() => setSelectedMemberId(null)}
                aria-label="关闭成员设置"
              />
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {selectedMember ? (
              <TeamMemberSettings
                member={selectedMember}
                workerMembers={workerMembers}
                agentNameById={agentNameById}
                onUpdate={(patch) => updateMember(selectedMember.id, patch)}
                onRemove={selectedMember.role === 'worker' ? () => removeWorker(selectedMember.id) : undefined}
              />
            ) : (
              <div className="flex h-full items-center justify-center">
                <Empty type="noData" description="选择成员后编辑设置" />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function TeamMemberSettings({
  member,
  workerMembers,
  agentNameById,
  onUpdate,
  onRemove,
}: {
  member: TeamMember;
  workerMembers: TeamMember[];
  agentNameById: Map<string, string>;
  onUpdate: (patch: Partial<TeamMember>) => void;
  onRemove?: () => void;
}) {
  const dependencyOptions = workerMembers
    .filter((worker) => worker.id !== member.id && worker.agentId !== member.agentId)
    .map((worker) => ({
      label: agentNameById.get(worker.agentId) || worker.name,
      value: worker.agentId,
    }));

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Tag size="small" color={member.role === 'leader' ? 'blue' : 'mauve'}>
                {member.role}
              </Tag>
              <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                {agentNameById.get(member.agentId) || member.name}
              </div>
            </div>
            <div className="mt-2 text-xs text-[var(--color-text-secondary)]">
              {member.agentId}
            </div>
          </div>
          {onRemove && (
            <Button size="small" danger icon={<Trash2 size={13} />} onClick={onRemove}>
              移除
            </Button>
          )}
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
          成员职责说明
        </label>
        <Input.TextArea
          value={member.description || ''}
          onChange={(event) => onUpdate({ description: event.target.value })}
          rows={4}
          placeholder="描述该成员在 Team 中负责什么"
        />
      </div>

      {member.role === 'worker' ? (
        <>
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              执行方式
            </label>
            <Select
              value={member.executionMode || 'parallel'}
              onChange={(value) => onUpdate({
                executionMode: value as TeamMember['executionMode'],
              })}
              options={[
                { label: '并行执行', value: 'parallel' },
                { label: '等待依赖 Worker', value: 'depends_on' },
              ]}
              style={{ width: '100%' }}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              依赖 Worker
            </label>
            <Select
              mode="multiple"
              value={member.dependsOn || []}
              disabled={member.executionMode !== 'depends_on'}
              onChange={(value) => onUpdate({ dependsOn: value })}
              options={dependencyOptions}
              placeholder="选择上游 Worker"
              style={{ width: '100%' }}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              交接提示
            </label>
            <Input.TextArea
              value={member.handoffPrompt || ''}
              onChange={(event) => onUpdate({ handoffPrompt: event.target.value })}
              rows={5}
              placeholder="告诉该 Worker 如何使用上游 Worker 的输出"
            />
          </div>
        </>
      ) : (
        <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4 text-sm text-[var(--color-text-secondary)]">
          Leader 负责拆解任务和汇总结果；成员增删请通过 Worker 列表管理。
        </div>
      )}
    </div>
  );
}
