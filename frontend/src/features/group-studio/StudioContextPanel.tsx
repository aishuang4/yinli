/** 右侧可折叠上下文：成员、置顶备注、文件。 */

import { useRef, useState, type KeyboardEvent } from 'react';
import { Download } from 'lucide-react';
import { StudioGoalModal } from './StudioGoalModal';
import { StudioMemberModal } from './StudioMemberModal';
import type {
  GroupStudioAgentOption,
  GroupStudioFile,
  GroupStudioMember,
  GroupStudioSnapshot,
} from './types';

interface StudioContextPanelProps {
  adding: boolean;
  addingAgentId: string;
  collapsed: boolean;
  files: GroupStudioFile[];
  goal: string;
  isMacDesktopShell: boolean;
  isWindowsDesktopShell: boolean;
  memberBusyId: string;
  memberOptions: GroupStudioAgentOption[];
  members: GroupStudioMember[];
  savingCard: boolean;
  speakingMemberId: string;
  studio: GroupStudioSnapshot | null;
  onAddMember: (agentId: string) => void;
  onMuteMember: (memberId: string, muted: boolean) => void;
  onRemoveMember: (memberId: string) => void;
  onSaveGoal: (goal: string) => void;
  onDownloadFile: (file: GroupStudioFile) => void;
  onOpenMembers: () => void;
}

export function StudioContextPanel({
  adding,
  addingAgentId,
  collapsed,
  files,
  goal,
  isMacDesktopShell,
  isWindowsDesktopShell,
  memberBusyId,
  memberOptions,
  members,
  savingCard,
  speakingMemberId,
  studio,
  onAddMember,
  onMuteMember,
  onRemoveMember,
  onSaveGoal,
  onDownloadFile,
  onOpenMembers,
}: StudioContextPanelProps) {
  const speakerQueue = studio?.speakerQueue ?? [];
  const readonly = !studio || studio.status === 'done' || studio.status === 'deleted';
  const [memberModalOpen, setMemberModalOpen] = useState(false);
  const [goalModalOpen, setGoalModalOpen] = useState(false);
  const clickLockRef = useRef(false);
  const modalOpen = memberModalOpen || goalModalOpen;

  const lockClicks = () => {
    clickLockRef.current = true;
    window.setTimeout(() => {
      clickLockRef.current = false;
    }, 280);
  };

  const openModal = (open: () => void) => {
    if (clickLockRef.current || modalOpen) {
      return;
    }
    open();
  };

  const closeMemberModal = () => {
    setMemberModalOpen(false);
    lockClicks();
  };
  const closeGoalModal = () => {
    setGoalModalOpen(false);
    lockClicks();
  };

  const openOnEnter = (event: KeyboardEvent<HTMLElement>, open: () => void) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openModal(open);
    }
  };

  return (
    <aside
      className="group-studio-context"
      data-collapsed={collapsed || undefined}
      data-mac-shell={isMacDesktopShell || undefined}
      data-modal-open={modalOpen}
      data-windows-shell={isWindowsDesktopShell || undefined}
    >
      <div className="group-studio-context__chrome">
        <div className="group-studio-context__title">成员与备注</div>
      </div>
      <div className="group-studio-context__body">
        <section
          className="group-studio-section group-studio-section--hit"
          role="button"
          tabIndex={studio ? 0 : -1}
          aria-label={readonly ? '查看成员' : '管理成员'}
          aria-disabled={!studio}
          onClick={() => {
            if (studio) {
              openModal(() => setMemberModalOpen(true));
              onOpenMembers();
            }
          }}
          onKeyDown={(event) => openOnEnter(event, () => {
            if (studio) {
              setMemberModalOpen(true);
              onOpenMembers();
            }
          })}
        >
          <div className="group-studio-section__head">
            <span>成员 · {members.length}</span>
          </div>
          <div className="group-studio-section__scroll">
            {members.length === 0 ? (
              <p className="group-studio-empty-hint">点这里拉人进来</p>
            ) : (
              <ul className="group-studio-member-list group-studio-member-list--preview">
                {members.map((member) => (
                  <li
                    key={member.id}
                    data-muted={member.muted}
                    data-queued={(speakerQueue.includes(member.id) && speakingMemberId !== member.id) || undefined}
                    data-next={speakingMemberId === member.id || undefined}
                  >
                    <span className="group-studio-member-list__avatar" aria-hidden="true">
                      {member.displayName.slice(0, 1)}
                    </span>
                    <span className="group-studio-member-list__name">{member.displayName}</span>
                    <span className="group-studio-member-list__role">
                      {member.status === 'crashed'
                        ? '崩溃'
                        : member.muted
                          ? '静音'
                      : speakingMemberId === member.id
                        ? '正在说'
                            : '空闲'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <section
          className="group-studio-section group-studio-section--hit"
          role="button"
          tabIndex={studio ? 0 : -1}
          aria-label="编辑置顶备注"
          aria-disabled={!studio}
          onClick={() => {
            if (studio) {
              openModal(() => setGoalModalOpen(true));
            }
          }}
          onKeyDown={(event) => openOnEnter(event, () => {
            if (studio) {
              setGoalModalOpen(true);
            }
          })}
        >
          <div className="group-studio-section__head">
            <span>置顶备注</span>
          </div>
          <div className="group-studio-section__scroll">
            <p className={`group-studio-goal-preview${goal.trim() ? '' : ' group-studio-empty-hint'}`}>
              {goal.trim() || '可空，不挡发言'}
            </p>
          </div>
        </section>

        <section className="group-studio-section">
          <div className="group-studio-section__head">
            <span>文件 · {files.length}</span>
          </div>
          <div className="group-studio-section__scroll">
            {files.length === 0 ? (
              <p className="group-studio-empty-hint">还没有文件</p>
            ) : (
              <ul className="group-studio-plain-list">
                {files.map((item) => (
                  <li key={item.id} className="group-studio-file-row">
                    <span className="min-w-0 truncate">{item.title}</span>
                    <span>{formatBytes(item.byteSize)}</span>
                    <button
                      type="button"
                      className="icon-button h-7 w-7 shrink-0"
                      aria-label={`下载 ${item.title}`}
                      title={`下载 ${item.title}`}
                      onClick={() => onDownloadFile(item)}
                    >
                      <Download size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>
      <StudioMemberModal
        open={memberModalOpen}
        members={members}
        memberOptions={memberOptions}
        adding={adding}
        addingAgentId={addingAgentId}
        canAddMembers={!readonly}
        memberBusyId={memberBusyId}
        onClose={closeMemberModal}
        onAddMember={onAddMember}
        onMuteMember={onMuteMember}
        onRemoveMember={onRemoveMember}
      />
      <StudioGoalModal
        open={goalModalOpen}
        value={goal}
        saving={savingCard}
        locked={readonly}
        onClose={closeGoalModal}
        onSave={(next) => {
          onSaveGoal(next);
          closeGoalModal();
        }}
      />
    </aside>
  );
}

function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
