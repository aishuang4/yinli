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
import { Bot, Download, Plus, Sparkles, Upload, Users, WandSparkles } from 'lucide-react';
import { Button, Empty, Modal, Spinner } from '@agentscope-ai/design';
import { useAgentBuilder } from '../hooks/useAgentBuilder';
import { useAgents } from '../hooks/useAgents';
import { useTeams } from '../hooks/useTeams';
import { AgentCard } from '../features/agents/AgentCard';
import { AgentEditor } from '../features/agents/AgentEditor';
import { AgentBuilderWizard } from '../features/teams/AgentBuilderWizard';
import { TeamDetailPanel } from '../features/teams/TeamDetailPanel';
import { TeamFormModal, type TeamFormState } from '../features/teams/TeamFormModal';
import { FileImportButton } from '../components/FileImportButton';
import { SelectField, TextField } from '../components/form';
import type { AgentCreatePayload } from '../types/agent';
import type { BuilderMode, BuilderPlan } from '../types/agentBuilder';

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
  const [teamForm, setTeamForm] = useState<TeamFormState>({
    name: '',
    description: '',
    leaderAgentId: '',
    workerAgentIds: [] as string[],
  });
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
              <FileImportButton
                size="small"
                accept="application/json,.json"
                icon={<Upload size={13} />}
                onFile={(file) => void handleImportAgentFile(file)}
              >
                导入
              </FileImportButton>
            </div>
          )}

          {mode === 'teams' && (
            <div className="mb-3 space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <Button size="small" disabled={!selectedTeam} onClick={() => void handleExportTeam()}>
                  <Download size={13} />
                  导出
                </Button>
                <FileImportButton
                  size="small"
                  accept="application/json,.json"
                  icon={<Upload size={13} />}
                  onFile={(file) => void handleImportTeamFile(file)}
                >
                  导入
                </FileImportButton>
              </div>
              <SelectField
                value={selectedTemplateId || undefined}
                onChange={(value) => setSelectedTemplateId(String(value))}
                options={teamTemplates.map((template) => ({
                  label: `${template.name}${template.builtin ? ' · 内置' : ''}`,
                  value: template.id,
                }))}
                placeholder="选择 Team 模板"
              />
              <TextField
                value={templateTeamName}
                onChange={(event) => setTemplateTeamName(event.target.value)}
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
          <div className="h-full min-h-0 p-4">
            <AgentEditor
              agent={selectedAgent}
              models={models}
              availableAgents={agents}
              onSave={handleUpdate}
              onCancel={() => setSelectedId(null)}
            />
          </div>
        ) : mode === 'teams' && selectedTeam ? (
          <TeamDetailPanel
            team={selectedTeam}
            agents={agents}
            onUpdate={async (payload) => {
              await updateTeam(selectedTeam.id, payload);
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

      <TeamFormModal
        open={createTeamOpen}
        form={teamForm}
        agents={agents}
        onFormChange={setTeamForm}
        onCancel={() => setCreateTeamOpen(false)}
        onCreate={() => void handleCreateTeam()}
      />

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
