/**
 * TeamFormModal — Team 创建表单弹窗。
 *
 * 统一 Team 基础信息和成员选择字段，减少 AgentTeamPage 内联表单体积。
 */

import { Modal } from '@agentscope-ai/design';
import { SelectField, TextField } from '../../components/form';
import type { Agent } from '../../types/agent';

export interface TeamFormState {
  name: string;
  description: string;
  leaderAgentId: string;
  memberAgentIds: string[];
}

interface TeamFormModalProps {
  open: boolean;
  form: TeamFormState;
  agents: Agent[];
  onFormChange: (form: TeamFormState) => void;
  onCancel: () => void;
  onCreate: () => void;
}

export function TeamFormModal({
  open,
  form,
  agents,
  onFormChange,
  onCancel,
  onCreate,
}: TeamFormModalProps) {
  return (
    <Modal
      title="创建 Team"
      open={open}
      onCancel={onCancel}
      onOk={onCreate}
      okText="创建"
      cancelText="取消"
    >
      <div className="space-y-3">
        <TextField
          label="名称"
          value={form.name}
          onChange={(event) => onFormChange({ ...form, name: event.target.value })}
          placeholder="如：Research Team"
        />
        <TextField
          label="描述"
          value={form.description}
          onChange={(event) => onFormChange({ ...form, description: event.target.value })}
          placeholder="Team 的目标或职责"
        />
        <SelectField
          label="Leader Agent"
          value={form.leaderAgentId}
          onChange={(value) => onFormChange({ ...form, leaderAgentId: String(value) })}
          options={agents.map((agent) => ({ label: agent.name, value: agent.id }))}
        />
        <SelectField
          label="候选角色 Agent"
          mode="multiple"
          value={form.memberAgentIds}
          onChange={(value) => onFormChange({ ...form, memberAgentIds: value as string[] })}
          options={agents
            .filter((agent) => agent.id !== form.leaderAgentId)
            .map((agent) => ({ label: agent.name, value: agent.id }))}
        />
      </div>
    </Modal>
  );
}
