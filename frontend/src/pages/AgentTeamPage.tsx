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

import { useState, useCallback } from 'react';
import { Plus, Bot, Users, Trash2 } from 'lucide-react';
import { Button, Empty, Input, Modal, Select, Spinner, Tag } from '@agentscope-ai/design';
import { useAgentMemory } from '../hooks/useAgentMemory';
import { useAgents } from '../hooks/useAgents';
import { useTeams } from '../hooks/useTeams';
import { AgentCard } from '../features/agents/AgentCard';
import { AgentEditor } from '../features/agents/AgentEditor';
import type { AgentCreatePayload } from '../types/agent';
import type { Agent } from '../types/agent';
import type { TeamRecord } from '../types/team';

/**
 * AgentTeamPage 组件
 */
export function AgentTeamPage() {
  const { agents, loading, models, createAgent, updateAgent, deleteAgent } = useAgents();
  const { teams, loading: teamsLoading, createTeam, deleteTeam } = useTeams();
  const [mode, setMode] = useState<'agents' | 'teams'>('agents');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [createTeamOpen, setCreateTeamOpen] = useState(false);
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
            ) : (
              <Button
                type="primary"
                size="small"
                icon={<Plus size={14} />}
                onClick={() => setCreateTeamOpen(true)}
                aria-label="创建 Team"
              />
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
                className={`w-full rounded-md px-3 py-2 text-left transition-colors ${
                  selectedTeamId === team.id
                    ? 'bg-[var(--color-bg-secondary)] ring-1 ring-[var(--color-accent)]'
                    : 'hover:bg-[var(--color-bg-hover)]'
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
    </div>
  );
}

function AgentMemoryPanel({ agent }: { agent: Agent }) {
  const { memory, loading, error } = useAgentMemory(agent.id);

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
      <div className="mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">长期记忆</h3>
        <div className="mt-2 space-y-2">
          {memory?.facts.length ? memory.facts.map((fact) => (
            <div key={fact.id} className="rounded-md bg-[var(--color-bg-secondary)] p-3">
              <div className="text-sm font-medium text-[var(--color-text-primary)]">{fact.title}</div>
              <div className="mt-1 text-xs text-[var(--color-text-secondary)]">{fact.content}</div>
            </div>
          )) : (
            <Empty type="noData" description="暂无长期记忆" />
          )}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">事件记忆</h3>
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
            <Empty type="noData" description="暂无事件记忆" />
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
