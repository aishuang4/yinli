/** 群聊工作室独立界面。布局对齐 Chat：左列表、中对话、右可折叠上下文。 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pause,
  Play,
  Plus,
  Settings,
  Square,
  Users,
} from 'lucide-react';
import { ScrollToBottomButton } from '../../components/ScrollToBottomButton';
import { useMessageScroll } from '../../components/useMessageScroll';
import { Spinner, Tag, Tooltip } from '@/components/design';
import { NotificationBell } from '../notifications/NotificationBell';
import { useAgents } from '../../hooks/useAgents';
import { useProjectResources } from '../../hooks/useProjectResources';
import { useAppStore } from '../../stores/appStore';
import { getErrorMessage, notifyError, notifySuccess } from '../../utils/feedback';
import { createId } from '../../utils/id';
import {
  addStudioMember,
  closeStudio,
  confirmStudioIntake,
  confirmStudioRestatement,
  createStudioFromChat,
  decideStudioApproval,
  fetchGroupStudio,
  fetchStudioFiles,
  fetchStudioList,
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
import { StudioComposer } from './StudioComposer';
import { StudioContextPanel } from './StudioContextPanel';
import { StudioHistoryPanel } from './StudioHistoryPanel';
import { StudioMessageList } from './StudioMessageList';
import type {
  GroupStudioApproval,
  GroupStudioBlackboard,
  GroupStudioFile,
  GroupStudioListItem,
  GroupStudioMember,
  GroupStudioSnapshot,
} from './types';
import {
  createLocalStudioId,
  createLocalStudioSnapshot,
  isLocalStudioId,
} from './localStudio';
import { useGroupStudioStream, type StudioTimelineMessage } from './useGroupStudioStream';
import './GroupStudioPage.css';

const HISTORY_PANEL_TRANSITION_MS = 260;

const STATUS_LABEL: Record<string, string> = {
  drafting: '对谈中',
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
  const openGroupStudio = useAppStore((state) => state.openGroupStudio);
  const studioSurface = useAppStore((state) => state.studioSurface);
  const localStudioSeed = useAppStore((state) => state.localStudioSeed);
  const activeProjectId = useAppStore((state) => state.activeProjectId);
  const activeWorkspaceId = useAppStore((state) => state.activeWorkspaceId);
  const setView = useAppStore((state) => state.setView);
  const { agents } = useAgents();
  const [studio, setStudio] = useState<GroupStudioSnapshot | null>(null);
  const [files, setFiles] = useState<GroupStudioFile[]>([]);
  const [studios, setStudios] = useState<GroupStudioListItem[]>([]);
  const [studiosLoading, setStudiosLoading] = useState(false);
  const { enabledByType } = useProjectResources(studio?.projectId || activeProjectId);
  const [error, setError] = useState('');
  const [runtimeError, setRuntimeError] = useState('');
  const [goal, setGoal] = useState('');
  const [savingCard, setSavingCard] = useState(false);
  const [adding, setAdding] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [memberBusyId, setMemberBusyId] = useState('');
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);

  const [addingAgentId, setAddingAgentId] = useState('');
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [historyPanelMounted, setHistoryPanelMounted] = useState(true);
  const [contextCollapsed, setContextCollapsed] = useState(false);
  const historyTransitionTimerRef = useRef<number | null>(null);
  const isDesktopShell = Boolean(window.electronAPI);
  const desktopPlatform = window.electronAPI?.getPlatform();
  const isMacDesktopShell = desktopPlatform === 'darwin';
  const isWindowsDesktopShell = desktopPlatform === 'win32';
  const isMac = desktopPlatform === 'darwin'
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
    if (isLocalStudioId(groupId)) {
      setError('');
      setRuntimeError('');
      setFiles([]);
      setStudio((current) => {
        if (current?.id === groupId) {
          return current;
        }
        const seed = localStudioSeed ?? { chatSessionId: createId(), agentId: 'default' };
        const agent = agents.find((item) => item.id === seed.agentId);
        return createLocalStudioSnapshot({
          id: groupId,
          projectId: activeProjectId,
          workspaceId: activeWorkspaceId,
          chatSessionId: seed.chatSessionId,
          agentId: seed.agentId,
          agentName: agent?.name,
        });
      });
      setGoal('');
      return;
    }
    let mounted = true;
    setError('');
    setRuntimeError('');
    void refreshStudio(groupId).catch((cause: unknown) => {
      if (mounted) {
        setError(cause instanceof Error ? cause.message : '无法加载工作室');
      }
    });
    return () => {
      mounted = false;
    };
  }, [activeProjectId, activeWorkspaceId, agents, groupId, localStudioSeed, refreshStudio, studioSurface]);

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
    setHistoryPanelMounted(true);
    setHistoryCollapsed(true);
    window.requestAnimationFrame(() => setHistoryCollapsed(false));
  }, []);

  const handleToggleContext = useCallback(() => {
    setContextCollapsed((value) => !value);
  }, []);

  const persistStudio = useCallback(async (
    current: GroupStudioSnapshot,
  ): Promise<GroupStudioSnapshot> => {
    if (!isLocalStudioId(current.id)) {
      return current;
    }
    const worker = current.members.find((item) => item.role === 'worker' && item.status !== 'removed');
    const created = await createStudioFromChat({
      projectId: current.projectId,
      chatSessionId: current.chatSessionId || createId(),
      agentId: worker?.agentId || 'default',
      draftText: '',
      filePaths: [],
    });
    openGroupStudio(created.id);
    setStudio(created);
    setGoal(created.taskCard.goal);
    await refreshStudioList();
    return created;
  }, [openGroupStudio, refreshStudioList]);

  const handleNewStudio = () => {
    const seed = { chatSessionId: createId(), agentId: 'default' };
    const localId = createLocalStudioId();
    const agent = agents.find((item) => item.id === 'default');
    setError('');
    setRuntimeError('');
    setFiles([]);
    setGoal('');
    setStudio(createLocalStudioSnapshot({
      id: localId,
      projectId: activeProjectId,
      workspaceId: activeWorkspaceId,
      chatSessionId: seed.chatSessionId,
      agentId: seed.agentId,
      agentName: agent?.name,
    }));
    openGroupStudio(localId, seed);
  };

  const { messages, status: liveStatus } = useGroupStudioStream(
    studio?.id || groupId,
    Boolean(studio && studioSurface !== 'none' && !isLocalStudioId(studio.id)),
    (event) => {
      if (event.type === 'GROUP_ERROR') {
        const message = String(event.payload.message || '工作室运行失败');
        setRuntimeError(message);
        notifyError(message);
        return;
      }
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
        return;
      }
      if (
        (event.type === 'GROUP_DOD'
          || event.type === 'GROUP_TASK_CARD'
          || event.type === 'GROUP_STATUS'
          || event.type === 'GROUP_INTAKE')
        && studio
      ) {
        void refreshStudio(studio.id).catch(() => undefined);
      }
    },
  );
  const members = (studio?.members ?? []).filter((member) => member.status !== 'removed');
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
        id: agent.id,
        name: agent.id === 'default' ? `${agent.name} · 默认助手` : agent.name,
        description: agent.description || '',
      }));
  }, [agents, enabledByType.agent, studio?.members]);

  const handleSaveGoal = async (nextGoal: string) => {
    if (!studio || !nextGoal.trim()) {
      return;
    }
    setSavingCard(true);
    try {
      let snapshot: GroupStudioSnapshot;
      if (isLocalStudioId(studio.id)) {
        snapshot = await persistStudio(studio);
        await updateTaskCard(snapshot.id, { projectId: snapshot.projectId, goal: nextGoal.trim() });
        snapshot = await refreshStudio(snapshot.id);
      } else {
        await updateTaskCard(studio.id, { projectId: studio.projectId, goal: nextGoal.trim() });
        snapshot = await refreshStudio(studio.id);
      }
      await refreshStudioList();
      notifySuccess(snapshot.status === 'drafting' ? '已记到任务卡，确认后才开始群聊。' : '任务卡已保存。');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法更新任务卡'));
    } finally {
      setSavingCard(false);
    }
  };

  const handleAddMember = async (agentId: string) => {
    if (!studio || !agentId) {
      return;
    }
    setAdding(true);
    setAddingAgentId(agentId);
    try {
      const persisted = await persistStudio(studio);
      await addStudioMember(persisted.id, { projectId: persisted.projectId, agentId });
      await refreshStudio(persisted.id);
      await refreshStudioList();
      notifySuccess('已加入工作室');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法加人'));
    } finally {
      setAdding(false);
      setAddingAgentId('');
    }
  };

  const handleMuteMember = async (memberId: string, muted: boolean) => {
    if (!studio) {
      return;
    }
    if (isLocalStudioId(studio.id)) {
      setStudio((current) => (
        current
          ? {
            ...current,
            members: current.members.map((item) => (
              item.id === memberId ? { ...item, muted } : item
            )),
          }
          : current
      ));
      notifySuccess(muted ? '已静音' : '已取消静音');
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
    if (isLocalStudioId(studio.id)) {
      setStudio((current) => (
        current
          ? {
            ...current,
            members: current.members.map((item) => (
              item.id === memberId ? { ...item, status: 'removed' as const } : item
            )),
          }
          : current
      ));
      notifySuccess('已移出工作室');
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
      const persisted = await persistStudio(studio);
      await uploadStudioFile(persisted.id, persisted.projectId, fileList[0]);
      await refreshStudio(persisted.id);
      await refreshStudioList();
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
      const persisted = await persistStudio(studio);
      await postStudioMessage(persisted.id, { projectId: persisted.projectId, body: draft.trim() });
      setDraft('');
      await refreshStudio(persisted.id);
      await refreshStudioList();
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
    if (isLocalStudioId(studio.id)) {
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
    if (!studio || isLocalStudioId(studio.id)) {
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
    if (!studio || isLocalStudioId(studio.id)) {
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

  const handleConfirmIntake = async () => {
    if (!studio || isLocalStudioId(studio.id) || !goal.trim()) {
      return;
    }
    try {
      if (studio.awaitingIntakeConfirm) {
        const snapshot = await confirmStudioIntake(studio.id, studio.projectId);
        setStudio(snapshot);
        setGoal(snapshot.taskCard.goal);
        notifySuccess('已开始群聊');
        return;
      }
      await postStudioMessage(studio.id, { projectId: studio.projectId, body: '确认并开始' });
      await refreshStudio(studio.id);
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法确认开始'));
    }
  };

  const handleClose = async () => {
    if (!studio) {
      return;
    }
    if (isLocalStudioId(studio.id)) {
      closeGroupStudio();
      setStudio(null);
      return;
    }
    try {
      const snapshot = await closeStudio(studio.id, studio.projectId, studio.status === 'closing');
      setStudio(snapshot);
      notifySuccess(
        snapshot.status === 'archived'
          ? '工作室已归档'
          : (snapshot.taskCard.dodChecklist ?? []).length > 0
            ? '已进入结项，请勾齐验收清单。'
            : '已进入结项。没有验收清单，可直接确认结项。',
      );
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法结项'));
    }
  };

  const handleToggleDod = async (itemId: string, checked: boolean) => {
    if (!studio) {
      return;
    }
    const checklist = (studio.taskCard.dodChecklist ?? []).map((item) => (
      item.id === itemId ? { ...item, checked } : item
    ));
    try {
      const persisted = await persistStudio(studio);
      await updateTaskCard(persisted.id, { projectId: persisted.projectId, dodChecklist: checklist });
      await refreshStudio(persisted.id);
      await refreshStudioList();
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法更新验收清单'));
    }
  };

  const handleAddDod = async (text: string) => {
    if (!studio || !text.trim()) {
      return;
    }
    const checklist = [
      ...(studio.taskCard.dodChecklist ?? []),
      { id: `gdod_${Date.now()}`, text: text.trim(), checked: false },
    ];
    try {
      const persisted = await persistStudio(studio);
      await updateTaskCard(persisted.id, { projectId: persisted.projectId, dodChecklist: checklist });
      await refreshStudio(persisted.id);
      await refreshStudioList();
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法添加验收项'));
    }
  };

  const pendingApproval = studio?.currentApproval?.status === 'pending' ? studio.currentApproval : null;
  const approvalMemberName = pendingApproval
    ? (members.find((item) => item.id === pendingApproval.memberId)?.displayName || '工人')
    : '';
  const statusText = studio
    ? (STATUS_LABEL[liveStatus || studio.status] || liveStatus || studio.status)
    : '加载中';
  const paused = studio?.status === 'paused' || studio?.status === 'circuit_broken';

  return (
    <div
      className="chat-page-shell chat-workbench-shell group-studio-shell grid h-full min-h-0 overflow-hidden"
      data-history-mounted={historyPanelMounted}
      data-history-collapsed={historyCollapsed}
      data-context-collapsed={contextCollapsed}
      data-electron-shell={isDesktopShell}
      data-windows-shell={isWindowsDesktopShell}
      style={{
        gridTemplateColumns: [
          historyPanelMounted ? (historyCollapsed ? '0px' : '286px') : '',
          'minmax(0, 1fr)',
          'auto',
        ].filter(Boolean).join(' '),
        transition: `grid-template-columns ${HISTORY_PANEL_TRANSITION_MS}ms var(--ease-out)`,
      }}
    >
      {historyPanelMounted ? (
        <StudioHistoryPanel
          activeGroupId={groupId}
          collapsed={historyCollapsed}
          isMacDesktopShell={isMacDesktopShell}
          loading={studiosLoading}
          studios={studios}
          statusLabel={(status) => STATUS_LABEL[status] || status}
          onCollapse={handleCollapseHistory}
          onNewStudio={() => void handleNewStudio()}
          onSelectStudio={(item) => openGroupStudio(item.id)}
        />
      ) : null}

      <section className="chat-main-panel chat-workbench-main grid min-h-0 grid-cols-1 overflow-hidden">
        <div className="grid min-h-0 grid-rows-[auto_1fr_auto] overflow-hidden">
          <header
            className="chat-workbench-topbar flex items-center justify-between gap-4 px-8 py-2"
            style={isMacDesktopShell && (!historyPanelMounted || historyCollapsed) ? { paddingLeft: 128 } : undefined}
          >
            <div className="flex min-w-0 items-center gap-3">
              {historyCollapsed ? (
                <>
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
                      onClick={() => void handleNewStudio()}
                      aria-label="新建群聊"
                    >
                      <Plus size={15} />
                    </button>
                  </Tooltip>
                </>
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
              <div className="min-w-0">
                <div className="truncate text-base font-semibold text-[var(--color-text-primary)]">
                  {studio?.title || '工作室'}
                </div>
                <div className="truncate text-xs text-[var(--color-text-tertiary)]">
                  {statusText}
                  {studio?.budget ? ` · 已用 ${studio.budget.usedTurns}/${studio.budget.maxTurns} 轮` : ''}
                </div>
              </div>
              {studio ? (
                <Tag className="chat-workbench-status-tag" size="small" color="transparent">
                  {statusText}
                </Tag>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {paused ? (
                <button
                  type="button"
                  className="icon-button chat-workbench-top-action inline-flex h-9 items-center gap-1.5 rounded-xl px-2.5 text-xs"
                  onClick={() => void handleResume()}
                  aria-label="继续工作室"
                >
                  <Play size={15} />
                  <span>继续</span>
                </button>
              ) : (
                <button
                  type="button"
                  className="icon-button chat-workbench-top-action inline-flex h-9 items-center gap-1.5 rounded-xl px-2.5 text-xs"
                  onClick={() => void handlePause()}
                  disabled={!studio}
                  aria-label="暂停工作室"
                >
                  <Pause size={15} />
                  <span>暂停</span>
                </button>
              )}
              <button
                type="button"
                className="icon-button chat-workbench-top-action inline-flex h-9 items-center gap-1.5 rounded-xl px-2.5 text-xs"
                onClick={() => void handleInterrupt()}
                disabled={!studio}
                aria-label="停止当前发言"
              >
                <Square size={14} />
                <span>停止</span>
              </button>
              {studio && (studio.status === 'reviewing' || studio.status === 'closing') ? (
                <button
                  type="button"
                  className="icon-button chat-workbench-top-action inline-flex h-9 items-center gap-1.5 rounded-xl px-2.5 text-xs"
                  onClick={() => void handleClose()}
                  aria-label={studio.status === 'closing' ? '确认结项' : '结束'}
                >
                  <Square size={14} />
                  <span>{studio.status === 'closing' ? '确认结项' : '结束'}</span>
                </button>
              ) : null}
              <Tooltip title={contextCollapsed ? '展开成员与黑板' : '折叠成员与黑板'} placement="bottom" mode="dark">
                <button
                  type="button"
                  className="icon-button chat-workbench-top-action h-9 w-9 rounded-xl"
                  onClick={handleToggleContext}
                  aria-label={contextCollapsed ? '展开成员与黑板' : '折叠成员与黑板'}
                >
                  {contextCollapsed ? <PanelRightOpen size={17} /> : <PanelRightClose size={17} />}
                </button>
              </Tooltip>
              <NotificationBell />
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
                    {studio?.status === 'drafting' && studio.awaitingIntakeConfirm ? (
                      <div className="group-studio-approval">
                        <div className="group-studio-approval__title">主持人认为可以开始了。确认后才会拉人开会。</div>
                        <div className="group-studio-approval__actions">
                          <button
                            type="button"
                            className="icon-button chat-workbench-top-action inline-flex h-9 items-center rounded-xl px-3 text-xs"
                            disabled={!goal.trim()}
                            onClick={() => void handleConfirmIntake()}
                          >
                            确认并开始
                          </button>
                        </div>
                      </div>
                    ) : null}
                    {studio?.awaitingRestatement ? (
                      <div className="group-studio-approval">
                        <div className="group-studio-approval__title">目标已变更，请确认主持人复述后再继续。</div>
                        <div className="group-studio-approval__actions">
                          <button
                            type="button"
                            className="icon-button chat-workbench-top-action inline-flex h-9 items-center rounded-xl px-3 text-xs"
                            onClick={() => void handleConfirmRestatement()}
                          >
                            确认复述
                          </button>
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
                          {studio?.status === 'drafting'
                            ? '先在下面跟主持人说说这次要做什么。目标和验收会出现在右侧，你确认后才开始群聊。'
                            : '主持人正在准备发言，或还没有消息。'}
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

          <StudioComposer
            disabled={!studio || sending || studio.status === 'circuit_broken'}
            sending={sending}
            value={draft}
            placeholder={
              studio?.status === 'paused'
                ? '暂停中，消息会先入列'
                : studio?.status === 'drafting'
                  ? '跟主持人说说目标、约束和验收…'
                  : '对工作室说话…'
            }
            onChange={setDraft}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void handleSend();
              }
            }}
            onSend={() => void handleSend()}
          />
        </div>
      </section>

      <div className="group-studio-context-slot" data-collapsed={contextCollapsed || undefined}>
        <StudioContextPanel
          adding={adding}
          addingAgentId={addingAgentId}
          collapsed={contextCollapsed}
          files={files}
          goal={goal}
          memberBusyId={memberBusyId}
          memberOptions={memberOptions}
          members={members}
          savingCard={savingCard}
          studio={studio}
          uploading={uploading}
          onAddDod={(text) => void handleAddDod(text)}
          onAddMember={(agentId) => void handleAddMember(agentId)}
          onMuteMember={(memberId, muted) => void handleMuteMember(memberId, muted)}
          onRemoveMember={(memberId) => void handleRemoveMember(memberId)}
          onConfirmIntake={() => void handleConfirmIntake()}
          onSaveGoal={(nextGoal) => void handleSaveGoal(nextGoal)}
          onToggleDod={(itemId, checked) => void handleToggleDod(itemId, checked)}
          onUploadFile={(fileList) => void handleUploadFile(fileList)}
        />
      </div>
    </div>
  );
}
