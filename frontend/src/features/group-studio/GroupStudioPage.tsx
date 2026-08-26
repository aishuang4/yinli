/** 群聊工作室独立界面。任务卡、黑板、成员静音与共享文件可编辑。 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Users } from 'lucide-react';
import { Button, Spinner } from '@/components/design';
import { SelectField, TextAreaField } from '../../components/form';
import { useAgents } from '../../hooks/useAgents';
import { useProjectResources } from '../../hooks/useProjectResources';
import { useAppStore } from '../../stores/appStore';
import { getErrorMessage, notifyError, notifySuccess } from '../../utils/feedback';
import {
  addStudioMember,
  confirmStudioRestatement,
  decideStudioApproval,
  fetchGroupStudio,
  fetchStudioFiles,
  interruptStudio,
  muteStudioMember,
  pauseStudio,
  postStudioMessage,
  removeStudioMember,
  resumeStudio,
  updateTaskCard,
  uploadStudioFile,
} from './api';
import { GroupApprovalBar } from './GroupApprovalBar';
import { StudioTopbar } from './StudioTopbar';
import type { GroupStudioApproval, GroupStudioBlackboard, GroupStudioFile, GroupStudioMember, GroupStudioSnapshot } from './types';
import { useGroupStudioStream } from './useGroupStudioStream';
import './GroupStudioPage.css';

const STATUS_LABEL: Record<string, string> = {
  drafting: '填写任务卡',
  discussing: '讨论中',
  executing: '执行中',
  reviewing: '验收中',
  awaiting_human: '等待你确认',
  paused: '已暂停',
  circuit_broken: '已熔断',
  closing: '待结项',
  archived: '已归档',
  failed: '失败',
};

export function GroupStudioPage() {
  const groupId = useAppStore((state) => state.activeGroupStudioId);
  const closeGroupStudio = useAppStore((state) => state.closeGroupStudio);
  const studioSurface = useAppStore((state) => state.studioSurface);
  const { agents } = useAgents();
  const [studio, setStudio] = useState<GroupStudioSnapshot | null>(null);
  const [files, setFiles] = useState<GroupStudioFile[]>([]);
  const { enabledByType } = useProjectResources(studio?.projectId || '');
  const [error, setError] = useState('');
  const [goal, setGoal] = useState('');
  const [savingCard, setSavingCard] = useState(false);
  const [adding, setAdding] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [memberBusyId, setMemberBusyId] = useState('');
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState('default');
  const isMac = window.electronAPI?.getPlatform() === 'darwin'
    || (!window.electronAPI && /Mac/i.test(navigator.platform));
  const shortcutLabel = isMac ? '⌘⇧G' : 'Ctrl+Shift+G';

  const refreshStudio = useCallback(async (targetId: string) => {
    const [snapshot, nextFiles] = await Promise.all([
      fetchGroupStudio(targetId),
      fetchStudioFiles(targetId),
    ]);
    setStudio(snapshot);
    setGoal(snapshot.taskCard.goal);
    setFiles(nextFiles);
    return snapshot;
  }, []);

  useEffect(() => {
    if (!groupId || studioSurface === 'none') {
      return;
    }
    let mounted = true;
    setError('');
    void refreshStudio(groupId).catch((cause: unknown) => {
      if (mounted) {
        setError(cause instanceof Error ? cause.message : '无法加载工作室');
      }
    });
    return () => {
      mounted = false;
    };
  }, [groupId, refreshStudio, studioSurface]);

  const { messages, status: liveStatus } = useGroupStudioStream(
    studio?.id || groupId,
    Boolean(studio && studioSurface !== 'none'),
    (event) => {
      if (event.type === 'GROUP_BLACKBOARD') {
        setStudio((current) => (
          current
            ? { ...current, blackboard: event.payload as unknown as GroupStudioBlackboard }
            : current
        ));
        return;
      }
      if (event.type === 'GROUP_MEMBER') {
        const member = event.payload as unknown as GroupStudioMember;
        setStudio((current) => {
          if (!current) {
            return current;
          }
          const exists = current.members.some((item) => item.id === member.id);
          return {
            ...current,
            members: exists
              ? current.members.map((item) => (item.id === member.id ? member : item))
              : [...current.members, member],
          };
        });
        return;
      }
      if (event.type === 'GROUP_FILE') {
        const file = event.payload as unknown as GroupStudioFile;
        setFiles((current) => (
          current.some((item) => item.id === file.id) ? current : [...current, file]
        ));
        return;
      }
      if (event.type === 'GROUP_APPROVAL') {
        const approval = event.payload as unknown as GroupStudioApproval;
        setStudio((current) => (
          current ? { ...current, currentApproval: approval } : current
        ));
        return;
      }
      if (event.type === 'GROUP_RUN_ENDED') {
        setStudio((current) => {
          if (!current || current.currentApproval?.status === 'pending') {
            return current;
          }
          return { ...current, currentApproval: null };
        });
        return;
      }
      if (event.type === 'GROUP_BUDGET') {
        setStudio((current) => (
          current ? { ...current, budget: event.payload as unknown as GroupStudioSnapshot['budget'] } : current
        ));
        return;
      }
      if (event.type === 'GROUP_CIRCUIT') {
        setStudio((current) => (
          current ? { ...current, status: 'circuit_broken' } : current
        ));
      }
    },
  );
  const members = (studio?.members ?? []).filter((member) => member.status !== 'removed');
  const timeline = messages.length > 0
    ? messages
    : (studio?.messages ?? []).filter((item) => item.visibleInTimeline !== false);
  const memberOptions = useMemo(() => {
    const occupied = new Set(
      (studio?.members ?? [])
        .filter((member) => member.status !== 'removed')
        .map((member) => member.agentId),
    );
    return agents
      .filter((agent) => (
        agent.role !== 'job'
        && (agent.id === 'default' || enabledByType.agent.has(agent.id))
        && !occupied.has(agent.id)
      ))
      .map((agent) => ({
        label: agent.id === 'default' ? `${agent.name} · 默认助手` : agent.name,
        value: agent.id,
      }));
  }, [agents, enabledByType.agent, studio?.members]);

  const handleSaveGoal = async () => {
    if (!studio) {
      return;
    }
    setSavingCard(true);
    try {
      await updateTaskCard(studio.id, { projectId: studio.projectId, goal });
      const snapshot = await refreshStudio(studio.id);
      notifySuccess(snapshot.status === 'discussing' ? '任务卡已生效，可以开始讨论。' : '任务卡已保存。');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法更新任务卡'));
    } finally {
      setSavingCard(false);
    }
  };

  const handleAddMember = async () => {
    if (!studio || !selectedAgentId) {
      return;
    }
    setAdding(true);
    try {
      await addStudioMember(studio.id, { projectId: studio.projectId, agentId: selectedAgentId });
      await refreshStudio(studio.id);
      notifySuccess('已加入工作室');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法加人'));
    } finally {
      setAdding(false);
    }
  };

  const handleMuteMember = async (memberId: string, muted: boolean) => {
    if (!studio) {
      return;
    }
    setMemberBusyId(memberId);
    try {
      await muteStudioMember(studio.id, memberId, { projectId: studio.projectId, muted });
      await refreshStudio(studio.id);
      notifySuccess(muted ? '已静音' : '已取消静音');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法更新静音'));
    } finally {
      setMemberBusyId('');
    }
  };

  const handleRemoveMember = async (memberId: string) => {
    if (!studio) {
      return;
    }
    setMemberBusyId(memberId);
    try {
      await removeStudioMember(studio.id, memberId, { projectId: studio.projectId });
      await refreshStudio(studio.id);
      notifySuccess('已移出工作室');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法移除成员'));
    } finally {
      setMemberBusyId('');
    }
  };

  const handleUploadFile = async (fileList: FileList | null) => {
    if (!studio || !fileList || fileList.length === 0) {
      return;
    }
    setUploading(true);
    try {
      await uploadStudioFile(studio.id, studio.projectId, fileList[0]);
      await refreshStudio(studio.id);
      notifySuccess('已加入共享文件库');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法上传文件'));
    } finally {
      setUploading(false);
    }
  };

  const handleApproval = async (decision: 'approve' | 'deny') => {
    if (!studio?.currentApproval) {
      return;
    }
    setApprovalBusy(true);
    try {
      const updated = await decideStudioApproval(studio.id, {
        projectId: studio.projectId,
        approvalId: studio.currentApproval.id,
        decision,
      });
      setStudio((current) => (current ? { ...current, currentApproval: updated } : current));
      notifySuccess(decision === 'approve' ? '已允许工人继续' : '已拒绝这次工具调用');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法处理审批'));
    } finally {
      setApprovalBusy(false);
    }
  };

  const handleSend = async () => {
    if (!studio || !draft.trim()) {
      return;
    }
    setSending(true);
    try {
      await postStudioMessage(studio.id, { projectId: studio.projectId, body: draft.trim() });
      setDraft('');
      await refreshStudio(studio.id);
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法发送消息'));
    } finally {
      setSending(false);
    }
  };

  const handlePause = async () => {
    if (!studio) {
      return;
    }
    try {
      const snapshot = await pauseStudio(studio.id, studio.projectId);
      setStudio(snapshot);
      notifySuccess('工作室已暂停');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法暂停'));
    }
  };

  const handleResume = async () => {
    if (!studio) {
      return;
    }
    try {
      const snapshot = await resumeStudio(studio.id, studio.projectId);
      setStudio(snapshot);
      notifySuccess('工作室已继续');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法恢复'));
    }
  };

  const handleInterrupt = async () => {
    if (!studio) {
      return;
    }
    try {
      await interruptStudio(studio.id, studio.projectId);
      await refreshStudio(studio.id);
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法停止'));
    }
  };

  const handleConfirmRestatement = async () => {
    if (!studio) {
      return;
    }
    try {
      const snapshot = await confirmStudioRestatement(studio.id, studio.projectId);
      setStudio(snapshot);
      notifySuccess('已确认新目标');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法确认复述'));
    }
  };

  const blackboard = studio?.blackboard;
  const openQuestions = (blackboard?.openQuestions ?? []).filter((item) => !item.resolved);
  const decisions = blackboard?.decisions ?? [];
  const pendingApproval = studio?.currentApproval?.status === 'pending' ? studio.currentApproval : null;
  const approvalMemberName = pendingApproval
    ? (members.find((item) => item.id === pendingApproval.memberId)?.displayName || '工人')
    : '';

  return (
    <div className="group-studio-shell">
      <StudioTopbar
        onBack={closeGroupStudio}
        shortcutLabel={shortcutLabel}
        title={studio?.title || '工作室'}
        statusLabel={studio ? (STATUS_LABEL[liveStatus || studio.status] || liveStatus || studio.status) : '加载中'}
        budgetLabel={studio?.budget ? `已用 ${studio.budget.usedTurns}/${studio.budget.maxTurns} 轮` : undefined}
        paused={studio?.status === 'paused' || studio?.status === 'circuit_broken'}
        onPause={studio ? () => void handlePause() : undefined}
        onResume={studio ? () => void handleResume() : undefined}
        onInterrupt={studio ? () => void handleInterrupt() : undefined}
      />
      {error ? (
        <div className="group-studio-error">{error}</div>
      ) : !studio && groupId ? (
        <div className="group-studio-loading">
          <Spinner />
          <span>正在打开工作室…</span>
        </div>
      ) : (
        <div className="group-studio-columns">
          <aside className="group-studio-pane group-studio-pane--members">
            <div className="group-studio-pane__label">成员</div>
            <ul className="group-studio-member-list">
              {members.map((member) => (
                <li key={member.id} data-role={member.role} data-muted={member.muted}>
                  <span className="group-studio-member-list__icon"><Users size={14} /></span>
                  <span className="min-w-0 truncate">
                    {member.displayName}
                    <span className="group-studio-member-list__role">
                      {member.role === 'host' ? '主持人' : member.muted ? '已静音' : '执行'}
                    </span>
                  </span>
                  {member.role === 'worker' ? (
                    <span className="group-studio-member-list__actions">
                      <button
                        type="button"
                        disabled={memberBusyId === member.id}
                        onClick={() => void handleMuteMember(member.id, !member.muted)}
                      >
                        {member.muted ? '唤醒' : '静音'}
                      </button>
                      <button
                        type="button"
                        disabled={memberBusyId === member.id}
                        onClick={() => void handleRemoveMember(member.id)}
                      >
                        移除
                      </button>
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
            <div className="group-studio-add-member">
              <SelectField
                label="加人"
                value={selectedAgentId}
                options={memberOptions}
                onChange={(value) => setSelectedAgentId(String(value))}
              />
              <Button
                size="small"
                loading={adding}
                disabled={adding || memberOptions.length === 0}
                onClick={() => void handleAddMember()}
              >
                加入
              </Button>
            </div>
          </aside>
          <section className="group-studio-pane group-studio-pane--timeline">
            <div className="group-studio-pane__label">时间线</div>
            {studio?.awaitingRestatement ? (
              <div className="group-studio-approval">
                <div className="group-studio-approval__title">目标已变更，请确认主持人复述后再继续。</div>
                <div className="group-studio-approval__actions">
                  <Button size="small" onClick={() => void handleConfirmRestatement()}>
                    确认复述
                  </Button>
                </div>
              </div>
            ) : null}
            {pendingApproval ? (
              <GroupApprovalBar
                approval={pendingApproval}
                memberName={approvalMemberName}
                submitting={approvalBusy}
                onDecide={(decision) => void handleApproval(decision)}
              />
            ) : null}
            {timeline.length === 0 ? (
              <p className="group-studio-placeholder">
                {studio?.status === 'drafting'
                  ? '先在右侧填写目标。没有目标时主持人不发言。'
                  : '主持人正在准备发言，或还没有消息。'}
              </p>
            ) : (
              <ol className="group-studio-timeline">
                {timeline.map((item) => (
                  <li key={item.id} data-role={item.role}>
                    <div className="group-studio-timeline__meta">
                      {item.role === 'host' ? '主持人' : item.role === 'human' ? '你' : item.role}
                    </div>
                    <div className="group-studio-timeline__body">{item.body || '…'}</div>
                  </li>
                ))}
              </ol>
            )}
            <div className="group-studio-composer">
              <textarea
                rows={3}
                value={draft}
                disabled={!studio || sending || studio.status === 'circuit_broken'}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={studio?.status === 'paused' ? '暂停中，消息会先入列' : '对工作室说话…'}
              />
              <Button
                size="small"
                loading={sending}
                disabled={sending || !studio || !draft.trim() || studio.status === 'circuit_broken'}
                onClick={() => void handleSend()}
              >
                发送
              </Button>
            </div>
          </section>
          <aside className="group-studio-pane group-studio-pane--board">
            <div className="group-studio-pane__label">任务卡</div>
            <TextAreaField
              label="目标"
              required
              rows={5}
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder="这间工作室要完成什么？"
            />
            <Button
              size="small"
              loading={savingCard}
              disabled={savingCard || !studio}
              onClick={() => void handleSaveGoal()}
            >
              保存任务卡
            </Button>
            <div className="group-studio-pane__label" style={{ marginTop: 20 }}>黑板</div>
            {decisions.length === 0 && openQuestions.length === 0 ? (
              <p className="group-studio-placeholder">共识决策会写在这里，不翻长聊天记录。</p>
            ) : (
              <div className="group-studio-board">
                {decisions.length > 0 ? (
                  <ul className="group-studio-board__list">
                    {decisions.map((item) => (
                      <li key={item.id}>{item.text}</li>
                    ))}
                  </ul>
                ) : null}
                {openQuestions.length > 0 ? (
                  <>
                    <div className="group-studio-pane__label">未决问题</div>
                    <ul className="group-studio-board__list">
                      {openQuestions.map((item) => (
                        <li key={item.id}>{item.text}</li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </div>
            )}
            <div className="group-studio-pane__label" style={{ marginTop: 20 }}>共享文件</div>
            {files.length === 0 ? (
              <p className="group-studio-placeholder">还没有文件。上传后工人和主持人都能看见。</p>
            ) : (
              <ul className="group-studio-file-list">
                {files.map((item) => (
                  <li key={item.id}>
                    <span className="min-w-0 truncate">{item.title}</span>
                    <span>{formatBytes(item.byteSize)}</span>
                  </li>
                ))}
              </ul>
            )}
            <label className="group-studio-upload">
              <input
                type="file"
                disabled={uploading || !studio}
                onChange={(event) => {
                  void handleUploadFile(event.target.files);
                  event.target.value = '';
                }}
              />
              <span>{uploading ? '上传中…' : '上传文件'}</span>
            </label>
          </aside>
        </div>
      )}
    </div>
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
