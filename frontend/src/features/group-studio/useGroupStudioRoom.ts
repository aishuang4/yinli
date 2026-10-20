/**
 * 群聊房间：加载、成员和发送。三栏折叠不在这里。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAgents } from '../../hooks/useAgents';
import { useAppStore } from '../../stores/appStore';
import { getErrorMessage, notifyError, notifySuccess } from '../../utils/feedback';
import { createId } from '../../utils/id';
import {
  addStudioMember,
  createStudioFromChat,
  decideStudioApproval,
  deleteStudio,
  fetchAvailableStudioAgents,
  fetchGroupStudio,
  friendlyGroupStudioError,
  fetchStudioFiles,
  fetchStudioList,
  downloadStudioFile,
  interruptStudio,
  muteStudioMember,
  postStudioMessage,
  removeStudioMember,
  rerunStudio,
  updateStudioPin,
  updateStudioModel,
  uploadStudioFile,
} from './api';
import { useModelCatalog } from '../models/useModelCatalog';
import type { StudioComposerAttachment } from './StudioComposer';
import type {
  GroupStudioAgentOption,
  GroupStudioApproval,
  GroupStudioFile,
  GroupStudioListItem,
  GroupStudioMember,
  GroupStudioSnapshot,
} from './types';
import { takeStudioComposerPrefill } from './useGroupStudioEntry';
import { useGroupStudioStream, type StudioTimelineMessage } from './useGroupStudioStream';
import { useMessageScroll } from '../../components/useMessageScroll';

export const STATUS_LABEL: Record<string, string> = {
  open: '进行中',
  paused: '已暂停',
  done: '已结束',
  deleted: '已删除',
};

const HOST_AGENT_ID = 'group-studio-host';
const MAX_DRAFT_MEMBERS = 6;

function makeLocalMember(agentId: string, displayName: string): GroupStudioMember {
  return {
    id: `local_${agentId}`,
    groupId: '',
    role: 'worker',
    agentId,
    displayName,
    status: 'active',
    muted: false,
  };
}

function isInvitableAgent(agentId: string, role?: string): boolean {
  return Boolean(agentId)
    && agentId !== 'default'
    && agentId !== HOST_AGENT_ID
    && role !== 'job';
}

function getGroupRuntimeErrorMessage(message: string): string {
  const normalized = message.toLowerCase();
  if (normalized.includes('insufficient balance') || normalized.includes('error code: 402')) {
    return '当前模型余额不足，工作室已暂停。请切换到有余额的模型或补充供应商 Key，然后再发一条恢复。';
  }
  if (normalized.includes('invalid api key') || normalized.includes('invalid_api_key') || normalized.includes('authentication')) {
    return '当前供应商 Key 无效，工作室已暂停。请修正 Key 或切换模型，然后再发一条恢复。';
  }
  return friendlyGroupStudioError(message, message);
}


export function useGroupStudioRoom() {
  const groupId = useAppStore((state) => state.activeGroupStudioId);
  const closeGroupStudio = useAppStore((state) => state.closeGroupStudio);
  const openGroupStudio = useAppStore((state) => state.openGroupStudio);
  const studioSurface = useAppStore((state) => state.studioSurface);
  const localStudioSeed = useAppStore((state) => state.localStudioSeed);
  const setView = useAppStore((state) => state.setView);
  const { catalog: modelCatalog, loading: modelsLoading, error: modelsError } = useModelCatalog();
  const { agents, refresh: refreshAgents } = useAgents();
  const isDraft = studioSurface !== 'none' && !groupId;
  const [studio, setStudio] = useState<GroupStudioSnapshot | null>(null);
  const [files, setFiles] = useState<GroupStudioFile[]>([]);
  const [studios, setStudios] = useState<GroupStudioListItem[]>([]);
  const [studiosLoading, setStudiosLoading] = useState(false);
  const [availableAgents, setAvailableAgents] = useState<GroupStudioAgentOption[]>([]);
  const [error, setError] = useState('');
  const [runtimeError, setRuntimeError] = useState('');
  const [goal, setGoal] = useState('');
  const [savingCard, setSavingCard] = useState(false);
  const [adding, setAdding] = useState(false);
  const [fileAttachments, setFileAttachments] = useState<StudioComposerAttachment[]>([]);
  const [memberBusyId, setMemberBusyId] = useState('');
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [draft, setDraft] = useState('');
  const [mentions, setMentions] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<GroupStudioListItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [draftMembers, setDraftMembers] = useState<GroupStudioMember[]>([]);
  const [draftModelId, setDraftModelId] = useState('');
  const [pendingOpenSend, setPendingOpenSend] = useState<{
    groupId: string;
    body: string;
    mentions: string[];
    files: StudioComposerAttachment[];
  } | null>(null);

  const [addingAgentId, setAddingAgentId] = useState('');

  const refreshStudio = useCallback(async (targetId: string) => {
    const [snapshot, nextFiles] = await Promise.all([
      fetchGroupStudio(targetId),
      fetchStudioFiles(targetId),
    ]);
    setStudio(snapshot);
    setGoal(snapshot.pin?.goal || '');
    setFiles(nextFiles);
    return snapshot;
  }, []);

  useEffect(() => {
    if (studioSurface === 'none') {
      return;
    }
    if (!groupId) {
      setStudio(null);
      setFiles([]);
      setGoal('');
      setDraftMembers([]);
      setMentions([]);
      setFileAttachments([]);
      setPendingOpenSend(null);
      setError('');
      setRuntimeError('');
      setDraftModelId(localStudioSeed?.modelConfigId || '');
      setDraft(takeStudioComposerPrefill());
      return;
    }
    let mounted = true;
    setError('');
    setRuntimeError('');
    const prefill = takeStudioComposerPrefill();
    if (prefill) {
      setDraft(prefill);
      setMentions([]);
    }
    void refreshStudio(groupId).catch((cause: unknown) => {
      if (mounted) {
        setError(cause instanceof Error ? cause.message : '无法加载工作室');
      }
    });
    return () => {
      mounted = false;
    };
  }, [groupId, localStudioSeed?.nonce, refreshStudio, studioSurface]);

  useEffect(() => {
    if (!isDraft || draftModelId || !modelCatalog?.defaultModelId) {
      return;
    }
    setDraftModelId(modelCatalog.defaultModelId);
  }, [draftModelId, isDraft, modelCatalog?.defaultModelId]);

  const refreshStudioList = useCallback(async () => {
    setStudiosLoading(true);
    try {
      setStudios(await fetchStudioList());
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法加载群聊列表'));
    } finally {
      setStudiosLoading(false);
    }
  }, []);

  useEffect(() => {
    if (studioSurface === 'none') {
      return;
    }
    void refreshStudioList();
  }, [refreshStudioList, studioSurface]);

  useEffect(() => {
    if (!isDraft) {
      return;
    }
    const agentId = localStudioSeed?.agentId || '';
    if (!isInvitableAgent(agentId)) {
      return;
    }
    const agent = agents.find((item) => item.id === agentId);
    if (!agent || !isInvitableAgent(agent.id, agent.role)) {
      return;
    }
    setDraftMembers((current) => (
      current.some((item) => item.agentId === agentId)
        ? current
        : [makeLocalMember(agent.id, agent.name), ...current]
    ));
  }, [agents, isDraft, localStudioSeed?.agentId, localStudioSeed?.nonce]);

  const handleNewStudio = () => {
    if (sending || Boolean(studio?.currentRunId)) {
      return;
    }
    setError('');
    setRuntimeError('');
    openGroupStudio(null, {
      chatSessionId: createId(),
      agentId: 'default',
      modelConfigId: modelCatalog?.defaultModelId,
      nonce: createId(),
    });
  };

  const handleDeleteStudio = async (item: GroupStudioListItem) => {
    setDeleteConfirm(item);
  };

  const handleConfirmDeleteStudio = async () => {
    if (!deleteConfirm) {
      return;
    }
    const item = deleteConfirm;
    setDeleting(true);
    try {
      await deleteStudio(item.id);
      await refreshStudioList();
      if (groupId === item.id) {
        const nextStudio = studios.find((studio) => studio.id !== item.id);
        if (nextStudio) {
          openGroupStudio(nextStudio.id);
        } else {
          openGroupStudio(null, {
            chatSessionId: createId(),
            agentId: 'default',
            modelConfigId: modelCatalog?.defaultModelId,
            nonce: createId(),
          });
        }
      }
      setDeleteConfirm(null);
      notifySuccess('群聊已删除');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法删除群聊'));
    } finally {
      setDeleting(false);
    }
  };

  const handleModelChange = async (modelConfigId: string) => {
    if (isDraft) {
      setDraftModelId(modelConfigId);
      return;
    }
    if (!studio) return;
    try {
      const updated = await updateStudioModel(studio.id, modelConfigId);
      setStudio(updated);
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法切换群聊模型'));
    }
  };

  const { messages, status: liveStatus, ready: streamReady } = useGroupStudioStream(
    studio?.id || groupId,
    Boolean(studio?.id && studioSurface !== 'none'),
    (event) => {
      if (event.type === 'GROUP_ERROR') {
        const code = String(event.payload.code || '');
        const message = String(event.payload.message || '工作室运行失败');
        const friendlyMessage = getGroupRuntimeErrorMessage(code || message);
        setRuntimeError(friendlyMessage);
        notifyError(friendlyMessage);
        return;
      }
      if (event.type === 'GROUP_PIN') {
        const pin = event.payload as { goal?: string; notes?: string; title?: string; updatedAt?: string };
        setGoal(pin.goal || '');
        setStudio((current) => (
          current
            ? { ...current, pin: { title: pin.title || '', goal: pin.goal || '', notes: pin.notes || '', updatedAt: pin.updatedAt || '' } }
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
          if (!current) {
            return current;
          }
          return {
            ...current,
            currentRunId: null,
            currentSpeakerMemberId: null,
            currentApproval: current.currentApproval?.status === 'pending' ? current.currentApproval : null,
          };
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
          current ? { ...current, status: 'paused', pauseReason: 'circuit' } : current
        ));
        return;
      }
      if (event.type === 'GROUP_FLOOR') {
        const memberId = String(event.payload.memberId || '');
        setStudio((current) => (
          current
            ? {
              ...current,
              currentSpeakerMemberId: memberId || null,
              currentRunId: String(event.payload.runId || current.currentRunId || '') || null,
            }
            : current
        ));
        return;
      }
      if (event.type === 'GROUP_SPEAKER_QUEUE') {
        const memberIds = Array.isArray(event.payload.memberIds)
          ? event.payload.memberIds.map((item) => String(item))
          : [];
        const pending = event.payload.pendingMemberId;
        setStudio((current) => (
          current
            ? {
              ...current,
              speakerQueue: memberIds,
              pendingSpeakerMemberId: pending == null ? (memberIds[0] || null) : String(pending),
              canRerun: current.status === 'open'
                && !current.currentRunId
                && memberIds.length === 0
                && Boolean(current.lastTurn?.kind),
            }
            : current
        ));
        return;
      }
      if (event.type === 'GROUP_STATUS' && studio) {
        const next = String(event.payload.to || event.status || '');
        if (next !== 'paused') {
          setRuntimeError('');
        }
        void refreshStudio(studio.id).catch(() => undefined);
      }
    },
  );
  const viewStudio = useMemo<GroupStudioSnapshot | null>(() => {
    if (studio) {
      return studio;
    }
    if (!isDraft) {
      return null;
    }
    return {
      id: '',
      chatSessionId: localStudioSeed?.chatSessionId || '',
      title: '',
      status: 'open',
      pin: { title: '', goal, notes: '', updatedAt: '' },
      members: draftMembers,
      modelConfigId: draftModelId || modelCatalog?.defaultModelId || '',
      messages: [],
      createdAt: '',
      updatedAt: '',
    };
  }, [
    draftMembers,
    draftModelId,
    goal,
    isDraft,
    localStudioSeed?.chatSessionId,
    modelCatalog?.defaultModelId,
    studio,
  ]);
  const members = (viewStudio?.members ?? []).filter((member) => member.status !== 'removed');
  const timeline: StudioTimelineMessage[] = messages.length > 0
    ? messages
    : (studio?.messages ?? [])
      .filter((item) => item.visibleInTimeline !== false)
      .map((item) => ({
        ...item,
        streaming: false,
        thinkingStreaming: false,
      }));
  const lastTimeline = timeline[timeline.length - 1];
  const isStreaming = Boolean(
    sending
    || lastTimeline?.streaming
    || lastTimeline?.thinkingStreaming
    || studio?.currentRunId,
  );
  const {
    cancelProgrammaticScroll,
    handleMessagesScroll,
    handleMessagesWheel,
    messagesContainerRef,
    messagesEndRef,
    resetScroll,
    scrollToBottom,
    showScrollToBottom,
  } = useMessageScroll({
    contentVersion: `${studio?.id || ''}:${timeline.length}:${lastTimeline?.id || ''}:${lastTimeline?.body.length || 0}:${lastTimeline?.thinking?.length || 0}`,
    isStreaming,
  });
  useEffect(() => {
    resetScroll();
  }, [groupId, resetScroll]);
  const speakingMemberId = useMemo(() => {
    const live = timeline.find((item) => (
      item.role === 'worker' && (item.streaming || item.thinkingStreaming)
    ));
    if (live?.speakerId) {
      return live.speakerId;
    }
    return studio?.currentSpeakerMemberId || '';
  }, [studio?.currentSpeakerMemberId, timeline]);

  const memberOptions = useMemo(() => {
    const seated = new Set(members.map((item) => item.agentId));
    if (isDraft) {
      return agents
        .filter((agent) => (
          isInvitableAgent(agent.id, agent.role)
          && !seated.has(agent.id)
        ))
        .map((agent) => ({
          id: agent.id,
          name: agent.name,
          description: agent.description || '',
        }));
    }
    return availableAgents.filter((agent) => agent.id !== 'default' && !seated.has(agent.id));
  }, [agents, availableAgents, isDraft, members]);

  const refreshAvailableAgents = useCallback((targetId: string) => {
    return fetchAvailableStudioAgents(targetId)
      .then(setAvailableAgents)
      .catch((cause: unknown) => notifyError(getErrorMessage(cause, '无法加载可加入的 Agent')));
  }, []);

  const handleSaveGoal = async (nextGoal: string) => {
    const trimmed = nextGoal.trim();
    if (isDraft) {
      setGoal(trimmed);
      notifySuccess('置顶备注已保存');
      return;
    }
    if (!studio) {
      return;
    }
    setSavingCard(true);
    try {
      await updateStudioPin(studio.id, { goal: trimmed });
      await refreshStudio(studio.id);
      await refreshStudioList();
      notifySuccess('置顶备注已保存');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法更新置顶备注'));
    } finally {
      setSavingCard(false);
    }
  };

  const handleAddMember = async (agentId: string) => {
    if (!agentId) {
      return;
    }
    if (isDraft) {
      if (draftMembers.length >= MAX_DRAFT_MEMBERS) {
        notifyError('工作室人数已达上限');
        return;
      }
      const agent = agents.find((item) => item.id === agentId);
      if (!agent || !isInvitableAgent(agent.id, agent.role)) {
        notifyError('这个人不能入群');
        return;
      }
      if (draftMembers.some((item) => item.agentId === agentId)) {
        notifyError('这个人已经在群里');
        return;
      }
      setDraftMembers((current) => [...current, makeLocalMember(agent.id, agent.name)]);
      notifySuccess('已加入群聊');
      return;
    }
    if (!studio || studio.status === 'done' || studio.status === 'deleted') {
      if (studio && (studio.status === 'done' || studio.status === 'deleted')) {
        notifyError('群聊已结束，不能再改成员');
      }
      return;
    }
    setAdding(true);
    setAddingAgentId(agentId);
    try {
      await addStudioMember(studio.id, { agentId });
      await refreshStudio(studio.id);
      await refreshAvailableAgents(studio.id);
      await refreshStudioList();
      notifySuccess('已加入群聊');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法加人'));
    } finally {
      setAdding(false);
      setAddingAgentId('');
    }
  };

  const handleOpenMembers = () => {
    if (isDraft) {
      void refreshAgents({ silent: true });
      return;
    }
    if (!studio) {
      return;
    }
    if (studio.status === 'done' || studio.status === 'deleted') {
      setAvailableAgents([]);
      return;
    }
    void refreshAvailableAgents(studio.id);
  };

  const handleMuteMember = async (memberId: string, muted: boolean) => {
    if (isDraft) {
      setDraftMembers((current) => current.map((item) => (
        item.id === memberId ? { ...item, muted } : item
      )));
      notifySuccess(muted ? '已静音' : '已取消静音');
      return;
    }
    if (!studio) {
      return;
    }
    setMemberBusyId(memberId);
    try {
      await muteStudioMember(studio.id, memberId, { muted });
      await refreshStudio(studio.id);
      notifySuccess(muted ? '已静音' : '已取消静音');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法更新静音'));
    } finally {
      setMemberBusyId('');
    }
  };

  const handleRemoveMember = async (memberId: string) => {
    if (isDraft) {
      setDraftMembers((current) => current.filter((item) => item.id !== memberId));
      setMentions((current) => current.filter((item) => item !== memberId));
      notifySuccess('已移出工作室');
      return;
    }
    if (!studio) {
      return;
    }
    setMemberBusyId(memberId);
    try {
      await removeStudioMember(studio.id, memberId);
      await refreshStudio(studio.id);
      await refreshAvailableAgents(studio.id);
      notifySuccess('已移出工作室');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法移除成员'));
    } finally {
      setMemberBusyId('');
    }
  };

  const handleComposerFile = (file: File) => {
    if (file.size > 50 * 1024 * 1024) {
      notifyError('单个附件不能超过 50 MB。');
      return;
    }
    setFileAttachments((current) => {
      if (current.length >= 10) {
        notifyError('单次最多上传 10 个附件。');
        return current;
      }
      if (current.some((item) => item.name === file.name && item.size === file.size)) {
        return current;
      }
      return [...current, { id: createId(), file, name: file.name, size: file.size }];
    });
  };

  const handleDownloadFile = async (file: GroupStudioFile) => {
    if (!studio) {
      return;
    }
    try {
      await downloadStudioFile(studio.id, file.id, file.title);
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法下载文件'));
    }
  };

  const handleApproval = async (decision: 'approve' | 'deny') => {
    if (!studio?.currentApproval) {
      return;
    }
    setApprovalBusy(true);
    try {
      const updated = await decideStudioApproval(studio.id, {
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

  const postToStudio = async (
    target: { id: string },
    body: string,
    nextMentions: string[],
    attachments: StudioComposerAttachment[],
  ) => {
    const uploaded: GroupStudioFile[] = [];
    for (const attachment of attachments) {
      uploaded.push(await uploadStudioFile(target.id, attachment.file));
    }
    const names = uploaded.map((item) => item.title).join('、');
    const text = body.trim() || (names ? `上传了文件：${names}` : '');
    await postStudioMessage(target.id, {
      body: text,
      mentions: nextMentions,
    });
    setDraft('');
    setMentions([]);
    setFileAttachments([]);
    await refreshStudio(target.id);
    await refreshStudioList();
  };

  const persistDraftStudio = async () => {
    const firstAgentId = draftMembers.find((item) => isInvitableAgent(item.agentId))?.agentId || 'default';
    const created = await createStudioFromChat({
      chatSessionId: localStudioSeed?.chatSessionId || createId(),
      agentId: firstAgentId,
      modelConfigId: draftModelId || modelCatalog?.defaultModelId,
      title: draft.trim().slice(0, 40) || undefined,
    });
    const mentionMap = new Map<string, string>();
    for (const member of draftMembers) {
      const seated = created.members.find((item) => (
        item.agentId === member.agentId && item.status !== 'removed'
      ));
      let live = seated;
      if (!live) {
        live = await addStudioMember(created.id, {
          agentId: member.agentId,
        });
      }
      mentionMap.set(member.id, live.id);
      if (member.muted && !live.muted) {
        await muteStudioMember(created.id, live.id, {
          muted: true,
        });
      }
    }
    if (goal.trim()) {
      await updateStudioPin(created.id, {
        goal: goal.trim(),
      });
    }
    const snapshot = await refreshStudio(created.id);
    return {
      created: snapshot,
      mentions: mentions.map((item) => mentionMap.get(item) || item).filter((item) => !item.startsWith('local_')),
    };
  };

  useEffect(() => {
    if (!pendingOpenSend || !streamReady || studio?.id !== pendingOpenSend.groupId) {
      return;
    }
    const pending = pendingOpenSend;
    const timer = window.setTimeout(() => {
      setPendingOpenSend(null);
      void postToStudio(
        { id: pending.groupId },
        pending.body,
        pending.mentions,
        pending.files,
      )
        .catch((cause: unknown) => notifyError(getErrorMessage(cause, '无法发送消息')))
        .finally(() => setSending(false));
    }, 80);
    return () => window.clearTimeout(timer);
  }, [pendingOpenSend, streamReady, studio?.id]);

  const handleSend = async () => {
    if ((!studio && !isDraft) || (!draft.trim() && fileAttachments.length === 0)) {
      return;
    }
    setSending(true);
    let waitingForStream = false;
    try {
      if (isDraft) {
        const persisted = await persistDraftStudio();
        setDraftMembers([]);
        openGroupStudio(persisted.created.id);
        setPendingOpenSend({
          groupId: persisted.created.id,
          body: draft.trim(),
          mentions: persisted.mentions,
          files: fileAttachments,
        });
        waitingForStream = true;
        return;
      }
      if (!studio) {
        return;
      }
      await postToStudio(studio, draft.trim(), mentions, fileAttachments);
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法发送消息'));
    } finally {
      if (!waitingForStream) {
        setSending(false);
      }
    }
  };

  const handleInterrupt = async () => {
    if (!studio) {
      return;
    }
    try {
      await interruptStudio(studio.id);
      await refreshStudio(studio.id);
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法停止'));
    }
  };

  const handleRerun = async () => {
    if (!studio?.canRerun) {
      return;
    }
    try {
      const next = await rerunStudio(studio.id);
      setStudio(next);
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法再来一轮'));
    }
  };

  const pendingApproval = studio?.currentApproval?.status === 'pending' ? studio.currentApproval : null;
  const approvalMemberName = pendingApproval
    ? (members.find((item) => item.id === pendingApproval.memberId)?.displayName || '工人')
    : '';
  const statusText = isDraft
    ? '发送后才会出现在列表里'
    : studio
      ? (STATUS_LABEL[liveStatus || studio.status] || liveStatus || studio.status)
      : '加载中';
  const ended = studio?.status === 'done' || studio?.status === 'deleted';
  const composerReady = isDraft || Boolean(studio);

  return {
    groupId,
    closeGroupStudio,
    openGroupStudio,
    studioSurface,
    localStudioSeed,
    setView,
    modelCatalog,
    modelsLoading,
    modelsError,
    agents,
    refreshAgents,
    isDraft,
    studio,
    setStudio,
    files,
    setFiles,
    studios,
    setStudios,
    studiosLoading,
    setStudiosLoading,
    availableAgents,
    setAvailableAgents,
    error,
    setError,
    runtimeError,
    setRuntimeError,
    goal,
    setGoal,
    savingCard,
    setSavingCard,
    adding,
    setAdding,
    fileAttachments,
    setFileAttachments,
    memberBusyId,
    setMemberBusyId,
    approvalBusy,
    setApprovalBusy,
    draft,
    setDraft,
    mentions,
    setMentions,
    sending,
    setSending,
    deleteConfirm,
    setDeleteConfirm,
    deleting,
    setDeleting,
    draftMembers,
    setDraftMembers,
    draftModelId,
    setDraftModelId,
    pendingOpenSend,
    setPendingOpenSend,
    addingAgentId,
    setAddingAgentId,
    refreshStudio,
    refreshStudioList,
    handleNewStudio,
    handleDeleteStudio,
    handleConfirmDeleteStudio,
    handleModelChange,
    messages,
    liveStatus,
    streamReady,
    viewStudio,
    members,
    timeline,
    lastTimeline,
    isStreaming,
    speakingMemberId,
    memberOptions,
    refreshAvailableAgents,
    handleSaveGoal,
    handleAddMember,
    handleOpenMembers,
    handleMuteMember,
    handleRemoveMember,
    handleComposerFile,
    handleDownloadFile,
    handleApproval,
    postToStudio,
    persistDraftStudio,
    handleSend,
    handleInterrupt,
    handleRerun,
    pendingApproval,
    approvalMemberName,
    statusText,
    ended,
    composerReady,
    cancelProgrammaticScroll,
    handleMessagesScroll,
    handleMessagesWheel,
    messagesContainerRef,
    messagesEndRef,
    scrollToBottom,
    showScrollToBottom,
  };
}
