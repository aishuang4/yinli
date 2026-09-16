/** 群聊工作室独立界面。布局对齐 Chat：左列表、中对话、右可折叠上下文。 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Settings,
  Square,
  Users,
} from 'lucide-react';
import { ScrollToBottomButton } from '../../components/ScrollToBottomButton';
import { useMessageScroll } from '../../components/useMessageScroll';
import { Modal, Spinner, Tooltip } from '@/components/design';
import { NotificationBell } from '../notifications/NotificationBell';
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
  updateStudioPin,
  updateStudioModel,
  uploadStudioFile,
} from './api';
import { GroupApprovalBar } from './GroupApprovalBar';
import { ModelSelector } from '../models/ModelSelector';
import { useModelCatalog } from '../models/useModelCatalog';
import { StudioComposer, type StudioComposerAttachment } from './StudioComposer';
import { StudioContextPanel } from './StudioContextPanel';
import { StudioHistoryPanel } from './StudioHistoryPanel';
import { StudioMessageList } from './StudioMessageList';
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
import './GroupStudioPage.css';

const HISTORY_PANEL_TRANSITION_MS = 260;
const HISTORY_AUTO_COLLAPSE_WIDTH = 1180;
const CONTEXT_AUTO_COLLAPSE_WIDTH = 940;

type ResponsiveCollapseTier = 0 | 1 | 2;
type ResponsivePanePreference = 'history' | 'context' | null;

function getResponsiveCollapseTier(width: number): ResponsiveCollapseTier {
  if (width < CONTEXT_AUTO_COLLAPSE_WIDTH) {
    return 2;
  }
  if (width < HISTORY_AUTO_COLLAPSE_WIDTH) {
    return 1;
  }
  return 0;
}

const STATUS_LABEL: Record<string, string> = {
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

export function GroupStudioPage() {
  const groupId = useAppStore((state) => state.activeGroupStudioId);
  const closeGroupStudio = useAppStore((state) => state.closeGroupStudio);
  const openGroupStudio = useAppStore((state) => state.openGroupStudio);
  const studioSurface = useAppStore((state) => state.studioSurface);
  const localStudioSeed = useAppStore((state) => state.localStudioSeed);
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const activeWorkspaceId = useAppStore((state) => state.activeWorkspaceId);
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
    projectId: string;
    body: string;
    mentions: string[];
    files: StudioComposerAttachment[];
  } | null>(null);

  const [addingAgentId, setAddingAgentId] = useState('');
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [historyPanelMounted, setHistoryPanelMounted] = useState(true);
  const [contextCollapsed, setContextCollapsed] = useState(false);
  const [responsiveCollapseTier, setResponsiveCollapseTier] = useState<ResponsiveCollapseTier>(0);
  const [responsivePanePreference, setResponsivePanePreference] = useState<ResponsivePanePreference>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const historyTransitionTimerRef = useRef<number | null>(null);
  const isDesktopShell = Boolean(window.electronAPI);
  const desktopPlatform = window.electronAPI?.getPlatform();
  const isMacDesktopShell = desktopPlatform === 'darwin';
  const isWindowsDesktopShell = desktopPlatform === 'win32';
  const isMac = desktopPlatform === 'darwin'
    || (!window.electronAPI && /Mac/i.test(navigator.platform));
  const shortcutLabel = isMac ? '⌘⇧G' : 'Ctrl+Shift+G';
  const responsiveHistoryCollapsed = responsiveCollapseTier >= 1
    && responsivePanePreference !== 'history';
  const responsiveContextCollapsed = responsiveCollapseTier === 2
    ? responsivePanePreference !== 'context'
    : responsiveCollapseTier === 1 && responsivePanePreference === 'history';
  const effectiveHistoryCollapsed = historyCollapsed || responsiveHistoryCollapsed;
  const effectiveContextCollapsed = contextCollapsed || responsiveContextCollapsed;

  useLayoutEffect(() => {
    const shell = shellRef.current;
    if (!shell) {
      return;
    }

    const updateTier = (width: number) => {
      setResponsiveCollapseTier(getResponsiveCollapseTier(width));
    };
    updateTier(shell.getBoundingClientRect().width);

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        updateTier(entry.contentRect.width);
      }
    });
    observer.observe(shell);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setResponsivePanePreference(null);
  }, [responsiveCollapseTier]);

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
      setStudios(await fetchStudioList(activeProjectId));
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法加载群聊列表'));
    } finally {
      setStudiosLoading(false);
    }
  }, [activeProjectId]);

  useEffect(() => {
    if (studioSurface === 'none') {
      return;
    }
    void refreshStudioList();
  }, [refreshStudioList, studioSurface]);

  const handleCollapseHistory = useCallback(() => {
    if (historyTransitionTimerRef.current !== null) {
      window.clearTimeout(historyTransitionTimerRef.current);
    }
    setResponsivePanePreference((current) => (current === 'history' ? null : current));
    setHistoryCollapsed(true);
    historyTransitionTimerRef.current = window.setTimeout(() => {
      setHistoryPanelMounted(false);
      historyTransitionTimerRef.current = null;
    }, HISTORY_PANEL_TRANSITION_MS);
  }, []);

  const handleExpandHistory = useCallback(() => {
    if (historyTransitionTimerRef.current !== null) {
      window.clearTimeout(historyTransitionTimerRef.current);
      historyTransitionTimerRef.current = null;
    }
    setResponsivePanePreference('history');
    setHistoryPanelMounted(true);
    setHistoryCollapsed(true);
    window.requestAnimationFrame(() => setHistoryCollapsed(false));
  }, []);

  const handleToggleContext = useCallback(() => {
    if (effectiveContextCollapsed) {
      setContextCollapsed(false);
      setResponsivePanePreference('context');
      return;
    }
    setContextCollapsed(true);
    setResponsivePanePreference((current) => (current === 'context' ? null : current));
  }, [effectiveContextCollapsed]);

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
      await deleteStudio(item.id, activeProjectId);
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
      const updated = await updateStudioModel(studio.id, studio.projectId, modelConfigId);
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
      projectId: activeProjectId,
      workspaceId: activeWorkspaceId,
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
    activeProjectId,
    activeWorkspaceId,
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
          && (!agent.workspaceId || agent.workspaceId === activeWorkspaceId)
        ))
        .map((agent) => ({
          id: agent.id,
          name: agent.name,
          description: agent.description || '',
        }));
    }
    return availableAgents.filter((agent) => agent.id !== 'default' && !seated.has(agent.id));
  }, [activeWorkspaceId, agents, availableAgents, isDraft, members]);

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
      await updateStudioPin(studio.id, { projectId: studio.projectId, goal: trimmed });
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
      await addStudioMember(studio.id, { projectId: studio.projectId, agentId });
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
      await removeStudioMember(studio.id, memberId, { projectId: studio.projectId });
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

  const postToStudio = async (
    target: { id: string; projectId: string },
    body: string,
    nextMentions: string[],
    attachments: StudioComposerAttachment[],
  ) => {
    const uploaded: GroupStudioFile[] = [];
    for (const attachment of attachments) {
      uploaded.push(await uploadStudioFile(target.id, target.projectId, attachment.file));
    }
    const names = uploaded.map((item) => item.title).join('、');
    const text = body.trim() || (names ? `上传了文件：${names}` : '');
    await postStudioMessage(target.id, {
      projectId: target.projectId,
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
      projectId: activeProjectId,
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
          projectId: created.projectId,
          agentId: member.agentId,
        });
      }
      mentionMap.set(member.id, live.id);
      if (member.muted && !live.muted) {
        await muteStudioMember(created.id, live.id, {
          projectId: created.projectId,
          muted: true,
        });
      }
    }
    if (goal.trim()) {
      await updateStudioPin(created.id, {
        projectId: created.projectId,
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
        { id: pending.groupId, projectId: pending.projectId },
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
          projectId: persisted.created.projectId,
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
      await interruptStudio(studio.id, studio.projectId);
      await refreshStudio(studio.id);
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法停止'));
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

  return (
    <div
      ref={shellRef}
      className="chat-page-shell chat-workbench-shell group-studio-shell grid h-full min-h-0 overflow-hidden"
      data-history-mounted={historyPanelMounted}
      data-history-collapsed={effectiveHistoryCollapsed}
      data-context-collapsed={effectiveContextCollapsed}
      data-electron-shell={isDesktopShell}
      data-windows-shell={isWindowsDesktopShell}
      style={{
        gridTemplateColumns: [
          historyPanelMounted && !effectiveHistoryCollapsed
            ? 'var(--group-studio-history-width)'
            : '0px',
          'minmax(0, 1fr)',
          effectiveContextCollapsed ? '0px' : 'var(--group-studio-context-width)',
        ].join(' '),
        transition: `grid-template-columns ${HISTORY_PANEL_TRANSITION_MS}ms var(--ease-out)`,
      }}
    >
      {historyPanelMounted ? (
        <StudioHistoryPanel
          activeGroupId={groupId}
          collapsed={effectiveHistoryCollapsed}
          isMacDesktopShell={isMacDesktopShell}
          loading={studiosLoading}
          studios={studios}
          statusLabel={(status) => STATUS_LABEL[status] || status}
          onCollapse={handleCollapseHistory}
          onNewStudio={handleNewStudio}
          newDisabled={sending || Boolean(studio?.currentRunId)}
          onSelectStudio={(item) => {
            setDraft('');
            setMentions([]);
            setFileAttachments([]);
            setPendingOpenSend(null);
            openGroupStudio(item.id);
          }}
          onDeleteStudio={(item) => void handleDeleteStudio(item)}
        />
      ) : null}

      <section className="chat-main-panel chat-workbench-main grid min-h-0 grid-cols-1 overflow-hidden">
        <div className="grid min-h-0 grid-rows-[auto_1fr_auto] overflow-hidden">
          <header
            className="chat-workbench-topbar flex items-center justify-between gap-4 px-8 py-2"
            style={isMacDesktopShell && (!historyPanelMounted || effectiveHistoryCollapsed) ? { paddingLeft: 128 } : undefined}
          >
            <div className="chat-workbench-title-group flex min-w-0 items-center gap-3">
              {effectiveHistoryCollapsed ? (
                <div className="flex shrink-0 items-center gap-2">
                  <Tooltip title="展开群聊列表" placement="bottom" mode="dark">
                    <button
                      type="button"
                      className="icon-button chat-history-collapse-button"
                      onClick={handleExpandHistory}
                      aria-label="展开群聊列表"
                    >
                      <PanelLeftOpen size={17} strokeWidth={1.7} />
                    </button>
                  </Tooltip>
                  <Tooltip title="新建群聊" placement="bottom" mode="dark">
                    <button
                      type="button"
                      className="icon-button chat-history-new-button"
                      onClick={handleNewStudio}
                      disabled={sending || Boolean(studio?.currentRunId)}
                      aria-label="新建群聊"
                    >
                      <Plus size={15} />
                    </button>
                  </Tooltip>
                </div>
              ) : null}
              <Tooltip title={`返回 Chat ${shortcutLabel}`} placement="bottom" mode="dark">
                <button
                  type="button"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)]"
                  onClick={closeGroupStudio}
                  aria-label="返回 Chat"
                >
                  <Users size={17} strokeWidth={1.7} />
                </button>
              </Tooltip>
              <div className="chat-workbench-title-copy min-w-0">
                <div className="truncate text-base font-semibold text-[var(--color-text-primary)]">
                  {isDraft ? '新群聊' : (studio?.title || '工作室')}
                </div>
                <div className="truncate text-xs text-[var(--color-text-tertiary)]">
                  {statusText}
                  {studio?.budget ? ` · 已用 ${studio.budget.usedTurns}/${studio.budget.maxTurns} 轮` : ''}
                </div>
              </div>
            </div>
            <div className="chat-workbench-controls flex min-w-0 shrink items-center gap-2">
              <Tooltip title={effectiveContextCollapsed ? '展开成员与备注' : '折叠成员与备注'} placement="bottom" mode="dark">
                <button
                  type="button"
                  className="icon-button chat-workbench-top-action h-9 w-9 rounded-xl"
                  onClick={handleToggleContext}
                  aria-label={effectiveContextCollapsed ? '展开成员与备注' : '折叠成员与备注'}
                >
                  {effectiveContextCollapsed ? <PanelRightOpen size={17} /> : <PanelRightClose size={17} />}
                </button>
              </Tooltip>
              <NotificationBell />
              <div className="chat-workbench-model-control min-w-0 shrink">
                <ModelSelector
                  catalog={modelCatalog}
                  value={viewStudio?.modelConfigId || modelCatalog?.defaultModelId || ''}
                  onChange={(value) => void handleModelChange(value)}
                  disabled={!composerReady || sending || ended || Boolean(studio?.currentRunId)}
                  loading={modelsLoading}
                  error={modelsError}
                />
              </div>
              <Tooltip title="工作台设置" placement="bottom" mode="dark">
                <button
                  type="button"
                  className="icon-button chat-workbench-top-action h-9 w-9 rounded-xl"
                  onClick={() => setView('settings')}
                  aria-label="进入工作台设置"
                >
                  <Settings size={17} />
                </button>
              </Tooltip>
            </div>
          </header>

          <div className="relative min-h-0 overflow-hidden">
            <div
              ref={messagesContainerRef}
              className="h-full min-h-0 overflow-auto px-8 py-4"
              onScroll={handleMessagesScroll}
              onWheel={handleMessagesWheel}
              onPointerDown={cancelProgrammaticScroll}
              role="log"
              aria-live="polite"
              aria-label="群聊消息"
            >
              <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-4">
                {error ? (
                  <div className="group-studio-runtime-error">{error}</div>
                ) : !studio && groupId ? (
                  <div className="chat-empty-state flex flex-1 flex-col items-center justify-center gap-3 text-center text-[var(--color-text-tertiary)]">
                    <Spinner />
                    <div>正在打开工作室…</div>
                  </div>
                ) : (
                  <>
                    {runtimeError ? <div className="group-studio-runtime-error">{runtimeError}</div> : null}
                    {studio?.pauseReason === 'circuit' && studio.status === 'paused' ? (
                      <div className="group-studio-approval" role="status">
                        <div className="group-studio-approval__title">
                          先停一下：这一轮说得有点多。再发一条就可以接着聊。
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
                      <div className="chat-empty-state flex flex-1 flex-col items-center justify-center gap-3 text-center text-[var(--color-text-tertiary)]">
                        <div className="chat-empty-state__icon">
                          <Users size={42} strokeWidth={1.2} />
                        </div>
                        <div className="text-lg font-medium">开始群聊</div>
                        <div className="max-w-[420px] text-sm">
                          先在右侧把人拉进来，再发一句。用 @ 菜单点名；不点名就是全员各说一轮。
                        </div>
                      </div>
                    ) : (
                      <>
                        <StudioMessageList members={members} messages={timeline} />
                        <div ref={messagesEndRef} />
                      </>
                    )}
                  </>
                )}
              </div>
            </div>
            <ScrollToBottomButton
              visible={showScrollToBottom}
              onClick={() => scrollToBottom('smooth')}
            />
          </div>

          {ended ? null : (
          <StudioComposer
            disabled={!composerReady || sending}
            sending={sending}
            value={draft}
            members={members}
            mentions={mentions}
            fileAttachments={fileAttachments}
            onFile={handleComposerFile}
            onRemoveFile={(id) => setFileAttachments((current) => current.filter((item) => item.id !== id))}
            placeholder={
              studio?.status === 'paused'
                ? '暂停中，发送会恢复并按这句开跑。用 @ 菜单点名。'
                : isDraft
                  ? '说一句才会创建群聊。用 @ 菜单点名；不点名就是全员各说一轮。'
                  : '说一句，或用 @ 菜单点名。不点名就是全员各说一轮。'
            }
            onChange={setDraft}
            onMentionsChange={setMentions}
            controls={(
              <Tooltip title="停止当前发言" placement="top" mode="dark">
                <button
                  type="button"
                  className="icon-button inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs"
                  onClick={() => void handleInterrupt()}
                  disabled={!studio}
                  aria-label="停止当前发言"
                >
                  <Square size={14} />
                  <span>停止</span>
                </button>
              </Tooltip>
            )}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void handleSend();
              }
            }}
            onSend={() => void handleSend()}
          />
          )}
        </div>
      </section>

      <div className="group-studio-context-slot" data-collapsed={effectiveContextCollapsed || undefined}>
        <StudioContextPanel
          adding={adding}
          addingAgentId={addingAgentId}
          collapsed={effectiveContextCollapsed}
          files={files}
          goal={goal}
          isMacDesktopShell={isMacDesktopShell}
          isWindowsDesktopShell={isWindowsDesktopShell}
          memberBusyId={memberBusyId}
          memberOptions={memberOptions}
          members={members}
          savingCard={savingCard}
          speakingMemberId={speakingMemberId}
          studio={viewStudio}
          onAddMember={(agentId) => void handleAddMember(agentId)}
          onMuteMember={(memberId, muted) => void handleMuteMember(memberId, muted)}
          onRemoveMember={(memberId) => void handleRemoveMember(memberId)}
          onSaveGoal={(nextGoal) => void handleSaveGoal(nextGoal)}
          onDownloadFile={(file) => void handleDownloadFile(file)}
          onOpenMembers={handleOpenMembers}
        />
      </div>
      <Modal
        title="删除群聊"
        open={Boolean(deleteConfirm)}
        onCancel={() => {
          if (!deleting) {
            setDeleteConfirm(null);
          }
        }}
        onOk={() => void handleConfirmDeleteStudio()}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: deleting }}
        cancelButtonProps={{ disabled: deleting }}
        destroyOnHidden
      >
        <p>
          确定删除“{deleteConfirm?.title || '未命名工作室'}”吗？删除后将从群聊列表移除。
        </p>
      </Modal>
    </div>
  );
}
