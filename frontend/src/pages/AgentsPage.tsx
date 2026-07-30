/**
 * AgentsPage — Agent 管理入口。
 *
 * 页面只管理持久 Agent，
 * Team 成员由一次性 TeamRun 的 Leader 在运行中动态创建。
 */

import { useCallback, useState } from 'react';
import { Modal } from '@/components/design';
import { useAgentBuilder } from '../hooks/useAgentBuilder';
import { useAgents } from '../hooks/useAgents';
import { AgentBuilderWizard } from '../features/agents/AgentBuilderWizard';
import { AgentDetailShell } from '../features/agents/AgentDetailShell';
import { AgentEditor } from '../features/agents/AgentEditor';
import { AgentLandingPage } from '../features/agents/AgentLandingPage';
import type { AgentCreatePayload } from '../types/agent';
import type { BuilderPlan } from '../types/agentBuilder';

export function AgentsPage() {
  const { agents, loading, models, createAgent, updateAgent, deleteAgent, refresh } = useAgents();
  const { planning, applying, error: builderError, createPlan, applyPlan } = useAgentBuilder();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [builderRequirement, setBuilderRequirement] = useState('');
  const [builderModel, setBuilderModel] = useState('');
  const [builderPlan, setBuilderPlan] = useState<BuilderPlan | null>(null);

  const selectedAgent = agents.find((agent) => agent.id === selectedId) || null;

  const goList = useCallback(() => {
    setCreating(false);
    setSelectedId(null);
  }, []);

  const handleCreate = useCallback(async (data: AgentCreatePayload) => {
    const newAgent = await createAgent(data);
    setSelectedId(newAgent.id);
    setCreating(false);
  }, [createAgent]);

  const handleUpdate = useCallback(async (data: AgentCreatePayload) => {
    if (!selectedId) return;
    await updateAgent(selectedId, data);
    await refresh();
  }, [refresh, selectedId, updateAgent]);

  const handleDelete = useCallback(async (id: string) => {
    await deleteAgent(id);
    if (selectedId === id) goList();
    setDeleteConfirm(null);
  }, [deleteAgent, goList, selectedId]);

  const handleCreateBuilderPlan = useCallback(async () => {
    if (!builderRequirement.trim()) return;
    const plan = await createPlan({
      requirement: builderRequirement.trim(),
      preferredModel: builderModel || undefined,
    });
    setBuilderPlan(plan);
  }, [builderModel, builderRequirement, createPlan]);

  const handleApplyBuilderPlan = useCallback(async () => {
    if (!builderPlan) return;
    const result = await applyPlan(builderPlan);
    await refresh();
    if (result.createdAgents[0]) {
      setSelectedId(result.createdAgents[0].id);
      setCreating(false);
    }
    setBuilderOpen(false);
    setBuilderPlan(null);
    setBuilderRequirement('');
  }, [applyPlan, builderPlan, refresh]);

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

  const modals = (
    <>
      <Modal
        title="确认删除"
        open={Boolean(deleteConfirm)}
        onCancel={() => setDeleteConfirm(null)}
        onOk={() => deleteConfirm && void handleDelete(deleteConfirm)}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
      >
        确定要删除这个 Agent 吗？此操作不可撤销。
      </Modal>

      <Modal
        title="智能创建 Agent"
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
          model={builderModel}
          models={models}
          plan={builderPlan}
          planning={planning}
          applying={applying}
          error={builderError}
          onRequirementChange={setBuilderRequirement}
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
        {modals}
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
          onDelete={selectedAgent.id === 'default' ? undefined : () => setDeleteConfirm(selectedAgent.id)}
        >
          <AgentEditor
            agent={selectedAgent}
            models={models}
            availableAgents={agents}
            onSave={handleUpdate}
            onCancel={goList}
          />
        </AgentDetailShell>
        {modals}
      </>
    );
  }

  return (
    <>
      <AgentLandingPage
        agents={agents}
        loading={loading}
        onCreateAgent={() => setCreating(true)}
        onBuilderOpen={() => setBuilderOpen(true)}
        onSelectAgent={(agentId) => {
          setSelectedId(agentId);
          setCreating(false);
        }}
        onDeleteAgent={setDeleteConfirm}
      />
      {modals}
    </>
  );
}
