/**
 * TeamDetailPanel — Team 调度和成员设置面板。
 *
 * 管理 Team 成员草稿、Worker Selector 提示词和单个成员协作参数。
 */

import { useEffect, useState } from 'react';
import { Sparkles, Trash2, UserPlus, X } from 'lucide-react';
import { Button, Empty, Tag } from '@agentscope-ai/design';
import { FormField, SelectField, TextAreaField } from '../../components/form';
import type { Agent } from '../../types/agent';
import type { TeamMember, TeamRecord } from '../../types/team';
import { createId } from '../../utils/id';

interface TeamDetailPanelProps {
  team: TeamRecord;
  agents: Agent[];
  onUpdate: (payload: { members: TeamMember[]; workerSelectorPrompt: string }) => Promise<void>;
  onDelete: () => Promise<void>;
}

export function TeamDetailPanel({
  team,
  agents,
  onUpdate,
  onDelete,
}: TeamDetailPanelProps) {
  const agentNameById = new Map(agents.map((agent) => [agent.id, agent.name]));
  const [memberDrafts, setMemberDrafts] = useState<TeamMember[]>(team.members);
  const [workerSelectorPrompt, setWorkerSelectorPrompt] = useState(team.workerSelectorPrompt || '');
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(team.members[0]?.id || null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setMemberDrafts(team.members);
    setWorkerSelectorPrompt(team.workerSelectorPrompt || '');
    setSelectedMemberId((current) => (
      current && team.members.some((member) => member.id === current)
        ? current
        : team.members[0]?.id || null
    ));
  }, [team.id, team.members, team.workerSelectorPrompt]);

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
      await onUpdate({
        members: memberDrafts,
        workerSelectorPrompt,
      });
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
          <FormField
            label={(
              <span className="flex items-center gap-1.5">
                <UserPlus size={13} />
                添加 Worker
              </span>
            )}
          >
            <SelectField
              key={memberDrafts.length}
              value={undefined}
              onChange={(value) => addWorker(String(value))}
              options={addableAgentOptions}
              placeholder={addableAgentOptions.length ? '选择一个 Agent 加入 Team' : '没有可添加的 Agent'}
              disabled={addableAgentOptions.length === 0}
            />
          </FormField>
        </div>

        <div className="mb-4 rounded-md bg-[var(--color-bg-secondary)] p-3 shadow-sm">
          <TextAreaField
            label={(
              <span className="flex items-center gap-1.5">
                <Sparkles size={13} />
                Worker 自动选择提示词
              </span>
            )}
            value={workerSelectorPrompt}
            onChange={(event) => setWorkerSelectorPrompt(event.target.value)}
            rows={8}
            placeholder="控制系统内置 TeamWorkerSelector 如何选择本轮参与的 Worker"
            help="该提示词只注入系统内置 Worker Selector Agent，不会赋予工具、MCP 或 Skills 权限。"
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

      <TextAreaField
        label="成员职责说明"
        value={member.description || ''}
        onChange={(event) => onUpdate({ description: event.target.value })}
        rows={4}
        placeholder="描述该成员在 Team 中负责什么"
      />

      {member.role === 'worker' ? (
        <>
          <SelectField
            label="执行方式"
            value={member.executionMode || 'parallel'}
            onChange={(value) => onUpdate({
              executionMode: value as TeamMember['executionMode'],
            })}
            options={[
              { label: '并行执行', value: 'parallel' },
              { label: '等待依赖 Worker', value: 'depends_on' },
            ]}
          />

          <SelectField
            label="依赖 Worker"
            mode="multiple"
            value={member.dependsOn || []}
            disabled={member.executionMode !== 'depends_on'}
            onChange={(value) => onUpdate({ dependsOn: value as string[] })}
            options={dependencyOptions}
            placeholder="选择上游 Worker"
          />

          <TextAreaField
            label="交接提示"
            value={member.handoffPrompt || ''}
            onChange={(event) => onUpdate({ handoffPrompt: event.target.value })}
            rows={5}
            placeholder="告诉该 Worker 如何使用上游 Worker 的输出"
          />
        </>
      ) : (
        <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4 text-sm text-[var(--color-text-secondary)]">
          Leader 负责拆解任务和汇总结果；成员增删请通过 Worker 列表管理。
        </div>
      )}
    </div>
  );
}
