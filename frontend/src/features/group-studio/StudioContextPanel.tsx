/** 右侧可折叠上下文：用现有表单组件，紧凑分区尽量一屏看完。 */

import { useRef, useState, type KeyboardEvent } from 'react';
import { Download } from 'lucide-react';
import { FileImportButton } from '../../components/FileImportButton';
import { StudioBlackboardModal } from './StudioBlackboardModal';
import { StudioDodModal } from './StudioDodModal';
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
  memberBusyId: string;
  memberOptions: GroupStudioAgentOption[];
  members: GroupStudioMember[];
  savingCard: boolean;
  studio: GroupStudioSnapshot | null;
  uploading: boolean;
  onAddDod: (text: string) => void;
  onAddMember: (agentId: string) => void;
  onConfirmIntake: () => void;
  onMuteMember: (memberId: string, muted: boolean) => void;
  onRemoveMember: (memberId: string) => void;
  onSaveGoal: (goal: string) => void;
  onToggleDod: (itemId: string, checked: boolean) => void;
  onUploadFile: (files: FileList | null) => void;
  onDownloadFile: (file: GroupStudioFile) => void;
  onOpenMembers: () => void;
}

export function StudioContextPanel({
  adding,
  addingAgentId,
  collapsed,
  files,
  goal,
  memberBusyId,
  memberOptions,
  members,
  savingCard,
  studio,
  uploading,
  onAddDod,
  onAddMember,
  onConfirmIntake,
  onMuteMember,
  onRemoveMember,
  onSaveGoal,
  onToggleDod,
  onUploadFile,
  onDownloadFile,
  onOpenMembers,
}: StudioContextPanelProps) {
  const blackboard = studio?.blackboard;
  const openQuestions = (blackboard?.openQuestions ?? []).filter((item) => !item.resolved);
  const decisions = blackboard?.decisions ?? [];
  const checklist = studio?.taskCard.dodChecklist ?? [];
  const speakerQueue = studio?.speakerQueue ?? [];
  const archived = studio?.status === 'archived';
  const cardLocked = !studio || studio.status === 'closing' || archived;
  const memberAddingLocked = !studio || ['drafting', 'closing', 'archived', 'failed'].includes(studio.status);
  const [memberModalOpen, setMemberModalOpen] = useState(false);
  const [goalModalOpen, setGoalModalOpen] = useState(false);
  const [dodModalOpen, setDodModalOpen] = useState(false);
  const [boardModalOpen, setBoardModalOpen] = useState(false);
  const clickLockRef = useRef(false);
  const modalOpen = memberModalOpen || goalModalOpen || dodModalOpen || boardModalOpen;

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
  const closeDodModal = () => {
    setDodModalOpen(false);
    lockClicks();
  };
  const closeBoardModal = () => {
    setBoardModalOpen(false);
    lockClicks();
  };

  const openOnEnter = (event: KeyboardEvent<HTMLElement>, open: () => void) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openModal(open);
    }
  };

  return (
    <aside className="group-studio-context" data-collapsed={collapsed || undefined} data-modal-open={modalOpen}>
      <section
        className="group-studio-section group-studio-section--hit"
        role="button"
        tabIndex={studio ? 0 : -1}
        aria-label={memberAddingLocked ? '查看成员（当前阶段不可修改）' : '管理成员'}
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
        <ul className="group-studio-member-list group-studio-member-list--preview">
          {members.map((member) => (
            <li
              key={member.id}
              data-muted={member.muted}
              data-queued={speakerQueue[0] === member.id || speakerQueue.includes(member.id) || undefined}
              data-next={speakerQueue[0] === member.id || undefined}
            >
              <span className="group-studio-member-list__avatar" aria-hidden="true">
                {member.displayName.slice(0, 1)}
              </span>
              <span className="group-studio-member-list__name">{member.displayName}</span>
              <span className="group-studio-member-list__role">
                {member.role === 'host' ? '主持' : member.muted ? '静音' : '执行'}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section
        className="group-studio-section group-studio-section--hit"
        role="button"
        tabIndex={studio ? 0 : -1}
        aria-label="编辑目标"
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
          <span>目标</span>
        </div>
        <p className={`group-studio-goal-preview${goal.trim() ? '' : ' group-studio-muted'}`}>
          {goal.trim() || '暂无'}
        </p>
        {studio?.status === 'drafting' ? (
          <button
            type="button"
            className="icon-button chat-workbench-top-action inline-flex h-8 items-center rounded-xl px-3 text-xs"
            disabled={!goal.trim() || !studio || cardLocked}
            onClick={(event) => {
              event.stopPropagation();
              onConfirmIntake();
            }}
          >
            {studio.awaitingIntakeConfirm ? '确认并开始' : '想开始的话点这里'}
          </button>
        ) : null}
      </section>

      <section
        className="group-studio-section group-studio-section--hit"
        role="button"
        tabIndex={studio ? 0 : -1}
        aria-label="管理验收"
        aria-disabled={!studio}
        onClick={() => {
          if (studio) {
            openModal(() => setDodModalOpen(true));
          }
        }}
        onKeyDown={(event) => openOnEnter(event, () => {
          if (studio) {
            setDodModalOpen(true);
          }
        })}
      >
        <div className="group-studio-section__head">
          <span>验收 · {checklist.length}</span>
        </div>
        <ul className="group-studio-dod group-studio-clip group-studio-clip--4">
          {checklist.length === 0 ? (
            <li className="group-studio-muted">暂无</li>
          ) : (
            checklist.map((item) => (
              <li key={item.id} data-checked={item.checked}>
                <span className="group-studio-dod__mark">{item.checked ? '✓' : '○'}</span>
                <span>{item.text}</span>
              </li>
            ))
          )}
        </ul>
      </section>

      <section className="group-studio-section">
        <div className="group-studio-section__head">
          <span>工作项 · {(studio?.workItems ?? []).length}</span>
        </div>
        <ul className="group-studio-dod group-studio-clip group-studio-clip--4">
          {(studio?.workItems ?? []).length === 0 ? (
            <li className="group-studio-muted">暂无</li>
          ) : (
            (studio?.workItems ?? []).map((item) => (
              <li key={item.id}>
                <span className="group-studio-dod__mark">
                  {item.status === 'done' ? '✓' : item.status === 'claimed' ? '●' : '○'}
                </span>
                <span>{item.title}</span>
              </li>
            ))
          )}
        </ul>
      </section>

      <section
        className="group-studio-section group-studio-section--hit"
        role="button"
        tabIndex={studio ? 0 : -1}
        aria-label="查看黑板"
        aria-disabled={!studio}
        onClick={() => {
          if (studio) {
            openModal(() => setBoardModalOpen(true));
          }
        }}
        onKeyDown={(event) => openOnEnter(event, () => {
          if (studio) {
            setBoardModalOpen(true);
          }
        })}
      >
        <div className="group-studio-section__head">
          黑板 · {decisions.length + openQuestions.length}
        </div>
        <ul className="group-studio-plain-list group-studio-clip group-studio-clip--4">
          {decisions.length === 0 && openQuestions.length === 0 ? (
            <li className="group-studio-muted">暂无</li>
          ) : (
            <>
              {decisions.map((item) => (
                <li key={item.id}>{item.text}</li>
              ))}
              {openQuestions.map((item) => (
                <li key={item.id} data-kind="question">待决 {item.text}</li>
              ))}
            </>
          )}
        </ul>
      </section>

      <section className="group-studio-section">
        <div className="group-studio-section__head">
          <span>文件 · {files.length}</span>
          <FileImportButton
            size="small"
            type="text"
            disabled={uploading || !studio}
            loading={uploading}
            onFile={(file) => {
              const transfer = new DataTransfer();
              transfer.items.add(file);
              onUploadFile(transfer.files);
            }}
          >
            上传
          </FileImportButton>
        </div>
        <ul className="group-studio-plain-list group-studio-clip group-studio-clip--3">
          {files.length === 0 ? (
            <li className="group-studio-muted">暂无</li>
          ) : (
            files.map((item) => (
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
            ))
          )}
        </ul>
      </section>
      <StudioMemberModal
        open={memberModalOpen}
        members={members}
        memberOptions={memberOptions}
        adding={adding}
        addingAgentId={addingAgentId}
        canAddMembers={!memberAddingLocked}
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
        locked={cardLocked}
        onClose={closeGoalModal}
        onSave={(next) => {
          onSaveGoal(next);
          closeGoalModal();
        }}
      />
      <StudioDodModal
        open={dodModalOpen}
        checklist={checklist}
        archived={archived}
        onClose={closeDodModal}
        onAdd={onAddDod}
        onToggle={onToggleDod}
      />
      <StudioBlackboardModal
        open={boardModalOpen}
        decisions={decisions}
        questions={blackboard?.openQuestions ?? []}
        onClose={closeBoardModal}
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
