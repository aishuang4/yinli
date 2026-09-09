/** 群聊成员管理弹窗：左侧可选 Agent 卡片，右侧已有成员。 */

import { Button, Modal } from '@/components/design';
import type { GroupStudioAgentOption, GroupStudioMember } from './types';

interface StudioMemberModalProps {
  adding: boolean;
  addingAgentId: string;
  canAddMembers: boolean;
  memberBusyId: string;
  memberOptions: GroupStudioAgentOption[];
  members: GroupStudioMember[];
  open: boolean;
  onAddMember: (agentId: string) => void;
  onClose: () => void;
  onMuteMember: (memberId: string, muted: boolean) => void;
  onRemoveMember: (memberId: string) => void;
}

export function StudioMemberModal({
  adding,
  addingAgentId,
  canAddMembers,
  memberBusyId,
  memberOptions,
  members,
  open,
  onAddMember,
  onClose,
  onMuteMember,
  onRemoveMember,
}: StudioMemberModalProps) {
  return (
    <Modal
      title="管理成员"
      open={open}
      onCancel={onClose}
      footer={null}
      width={840}
      className="group-studio-member-modal"
      centered
      destroyOnHidden
      styles={{ body: { overflow: 'hidden', paddingTop: 12, paddingBottom: 4 } }}
    >
      <div className="group-studio-member-dialog">
        <section className="group-studio-member-dialog__pane">
          <div className="group-studio-member-dialog__title">可加入</div>
          <div className="group-studio-member-dialog__body">
            {!canAddMembers ? (
              <p className="group-studio-member-dialog__notice" role="status">
                当前工作室阶段不允许添加成员。
              </p>
            ) : memberOptions.length === 0 ? (
              <p className="group-studio-muted">没有可加入的 Agent，或人数已达上限。</p>
            ) : (
              <div className="group-studio-agent-grid">
                {memberOptions.map((agent) => {
                  const pending = adding && addingAgentId === agent.id;
                  return (
                    <button
                      key={agent.id}
                      type="button"
                      className="group-studio-agent-card"
                      disabled={adding || !canAddMembers}
                      data-pending={pending}
                      onClick={() => {
                        if (canAddMembers) {
                          onAddMember(agent.id);
                        }
                      }}
                    >
                      <span className="group-studio-agent-card__name">{agent.name}</span>
                      <span className="group-studio-agent-card__desc">
                        {pending ? '正在加入…' : (agent.description || '暂无描述')}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </section>
        <section className="group-studio-member-dialog__pane">
          <div className="group-studio-member-dialog__title">已在群里 · {members.length}</div>
          <div className="group-studio-member-dialog__body">
            <ul className="group-studio-member-list group-studio-member-list--manage">
              {members.map((member) => (
                <li key={member.id} data-muted={member.muted}>
                  <span className="group-studio-member-list__avatar" aria-hidden="true">
                    {member.displayName.slice(0, 1)}
                  </span>
                  <span className="min-w-0">
                    <span className="group-studio-member-list__name">{member.displayName}</span>
                    <span className="group-studio-member-list__role">
                      {member.role === 'host' ? '主持人' : member.muted ? '已静音' : '执行'}
                    </span>
                  </span>
                  {member.role === 'worker' ? (
                    <span className="group-studio-member-list__actions">
                      <Button
                        size="small"
                        disabled={memberBusyId === member.id}
                        onClick={() => onMuteMember(member.id, !member.muted)}
                      >
                        {member.muted ? '唤醒' : '静音'}
                      </Button>
                      <Button
                        size="small"
                        danger
                        disabled={memberBusyId === member.id}
                        onClick={() => onRemoveMember(member.id)}
                      >
                        移除
                      </Button>
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>
    </Modal>
  );
}
