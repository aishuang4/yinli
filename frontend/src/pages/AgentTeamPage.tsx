/**
 * AgentTeamPage — 智能体入口与详情设置页面。
 *
 * 第一层只展示自定义智能体和协作 Team 入口；
 * 点击具体对象后进入第二层，再展示配置表单或 Team 调度设置。
 */

import { useState, useCallback } from 'react';
import { Modal } from '@agentscope-ai/design';
import { useAgentBuilder } from '../hooks/useAgentBuilder';
import { useAgents } from '../hooks/useAgents';
import { useTeams } from '../hooks/useTeams';
import { AgentDetailShell } from '../features/agents/AgentDetailShell';
import { AgentEditor } from '../features/agents/AgentEditor';
import { AgentLandingPage } from '../features/agents/AgentLandingPage';
import { AgentBuilderWizard } from '../features/teams/AgentBuilderWizard';
import { TeamDetailPanel } from '../features/teams/TeamDetailPanel';
import { TeamFormModal, type TeamFormState } from '../features/teams/TeamFormModal';
import type { AgentCreatePayload } from '../types/agent';
import type { BuilderMode, BuilderPlan } from '../types/agentBuilder';

export function AgentTeamPage() {
  const { agents, loading, models, createAgent, updateAgent, deleteAgent, refresh: refreshAgents } = useAgents();
  const { teams, loading: teamsLoading, createTeam, updateTeam, deleteTeam, refresh: refreshTeams } = useTeams();
  const { planning, applying, error: builderError, createPlan, applyPlan } = useAgentBuilder();
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
    memberAgentIds: [],
  });

  const selectedAgent = agents.find((agent) => agent.id === selectedId) || null;
  const selectedTeam = teams.find((team) => team.id === selectedTeamId) || null;

  const goList = useCallback(() => {
    setCreating(false);
    setSelectedId(null);
    setSelectedTeamId(null);
  }, []);

  const handleCreate = useCallback(async (data: AgentCreatePayload) => {
    const newAgent = await createAgent(data);
    setSelectedId(newAgent.id);
    setSelectedTeamId(null);
    setCreating(false);
  }, [createAgent]);

  const handleUpdate = useCallback(async (data: AgentCreatePayload) => {
    if (!selectedId) return;
    await updateAgent(selectedId, data);
    await refreshAgents();
  }, [refreshAgents, selectedId, updateAgent]);

  const handleDelete = useCallback(async (id: string) => {
    await deleteAgent(id);
    if (selectedId === id) {
      goList();
    }
    setDeleteConfirm(null);
  }, [deleteAgent, goList, selectedId]);

  const handleCreateTeam = useCallback(async () => {
    if (!teamForm.name.trim() || !teamForm.leaderAgentId) return;
    const team = await createTeam({
      name: teamForm.name.trim(),
      description: teamForm.description.trim(),
      leaderAgentId: teamForm.leaderAgentId,
      memberAgentIds: teamForm.memberAgentIds,
    });
    setSelectedTeamId(team.id);
    setSelectedId(null);
    setCreating(false);
    setCreateTeamOpen(false);
    setTeamForm({
      name: '',
      description: '',
      leaderAgentId: '',
      memberAgentIds: [],
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
      setSelectedTeamId(result.createdTeam.id);
      setSelectedId(null);
      setCreating(false);
    } else if (result.createdAgents[0]) {
      setSelectedId(result.createdAgents[0].id);
      setSelectedTeamId(null);
      setCreating(false);
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

  const detailModals = (
    <>
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
    </>
  );

  if (creating) {
    return (
      <>
        <AgentEditor
          agent={null}
          models={models}
          availableAgents={agents}
          onSave={handleCreate}
          onCancel={goList}
        />
        {detailModals}
      </>
    );
  }

  if (selectedAgent) {
    return (
      <>
        <AgentDetailShell
          title={selectedAgent.name}
          subtitle={selectedAgent.description || '配置这个智能体的模型、工具和运行边界'}
          onBack={goList}
          onExport={() => void handleExportAgent()}
          onDelete={() => setDeleteConfirm(selectedAgent.id)}
        >
          <AgentEditor
            agent={selectedAgent}
            models={models}
            availableAgents={agents}
            onSave={handleUpdate}
            onCancel={goList}
          />
        </AgentDetailShell>
        {detailModals}
      </>
    );
  }

  if (selectedTeam) {
    return (
      <>
        <AgentDetailShell
          title={selectedTeam.name}
          subtitle={selectedTeam.description || '配置 Leader 和候选角色素材'}
          onBack={goList}
          onExport={() => void handleExportTeam()}
        >
          <TeamDetailPanel
            team={selectedTeam}
            agents={agents}
            onUpdate={async (payload) => {
              await updateTeam(selectedTeam.id, payload);
              await refreshTeams();
            }}
            onDelete={async () => {
              await deleteTeam(selectedTeam.id);
              goList();
            }}
          />
        </AgentDetailShell>
        {detailModals}
      </>
    );
  }

  return (
    <>
      <AgentLandingPage
        agents={agents}
        teams={teams}
        loading={loading}
        teamsLoading={teamsLoading}
        onCreateAgent={() => setCreating(true)}
        onBuilderOpen={() => setBuilderOpen(true)}
        onCreateTeam={() => setCreateTeamOpen(true)}
        onSelectAgent={(agentId) => {
          setSelectedId(agentId);
          setSelectedTeamId(null);
          setCreating(false);
        }}
        onDeleteAgent={setDeleteConfirm}
        onSelectTeam={(teamId) => {
          setSelectedTeamId(teamId);
          setSelectedId(null);
          setCreating(false);
        }}
      />
      {detailModals}
    </>
  );
}
