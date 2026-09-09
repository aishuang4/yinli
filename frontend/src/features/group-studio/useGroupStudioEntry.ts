/** Chat 进入/返回工作室：探活、from-chat、上传附件、热键。 */

import { useCallback, useEffect, useState, type RefObject } from 'react';
import { useAppStore } from '../../stores/appStore';
import { createId } from '../../utils/id';
import { getErrorMessage, notifyError } from '../../utils/feedback';
import type { ChatFileAttachment } from '../chat/types';
import {
  createStudioFromChat,
  fetchGroupStudioSettings,
  fetchStudioByChat,
} from './api';
import { createLocalStudioId } from './localStudio';
import type { GroupStudioSnapshot } from './types';
import { useGroupStudioHotkey } from './useGroupStudioHotkey';

interface UseGroupStudioEntryOptions {
  activeProjectId: string;
  activeSessionId: string | null;
  agentId: string;
  modelConfigId?: string;
  chatRunBlocking: boolean;
  draftText: string;
  fileAttachments: ChatFileAttachment[];
  hasImages: boolean;
  isComposingRef: RefObject<boolean>;
  setActiveSessionId: (sessionId: string) => void;
  uploadFilesForSession: (
    files: ChatFileAttachment[],
    projectId: string,
    sessionId: string,
  ) => Promise<Array<{ path: string }>>;
}

export function useGroupStudioEntry({
  activeProjectId,
  activeSessionId,
  agentId,
  modelConfigId,
  chatRunBlocking,
  draftText,
  fileAttachments,
  hasImages,
  isComposingRef,
  setActiveSessionId,
  uploadFilesForSession,
}: UseGroupStudioEntryOptions) {
  const workspaceMode = useAppStore((state) => state.workspaceMode);
  const studioSurface = useAppStore((state) => state.studioSurface);
  const studioTransition = useAppStore((state) => state.studioTransition);
  const openGroupStudio = useAppStore((state) => state.openGroupStudio);
  const closeGroupStudio = useAppStore((state) => state.closeGroupStudio);
  const [flagEnabled, setFlagEnabled] = useState(false);
  const [existingStudio, setExistingStudio] = useState<GroupStudioSnapshot | null>(null);
  const [entering, setEntering] = useState(false);
  const isMac = window.electronAPI?.getPlatform() === 'darwin'
    || (!window.electronAPI && /Mac/i.test(navigator.platform));
  const shortcutLabel = isMac ? '⌘⇧G' : 'Ctrl+Shift+G';
  const inStudio = studioSurface === 'group-studio' || studioTransition.startsWith('enter');
  const busy = studioTransition !== 'idle' || entering;

  useEffect(() => {
    let mounted = true;
    void fetchGroupStudioSettings()
      .then((settings) => {
        if (mounted) {
          setFlagEnabled(Boolean(settings.enabled));
        }
      })
      .catch(() => {
        if (mounted) {
          setFlagEnabled(false);
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!flagEnabled || !activeSessionId) {
      setExistingStudio(null);
      return;
    }
    let mounted = true;
    void fetchStudioByChat(activeSessionId)
      .then((studio) => {
        if (mounted) {
          setExistingStudio(studio);
        }
      })
      .catch(() => {
        if (mounted) {
          setExistingStudio(null);
        }
      });
    return () => {
      mounted = false;
    };
  }, [activeSessionId, flagEnabled, studioSurface]);

  const enterStudio = useCallback(async () => {
    if (!flagEnabled || busy || workspaceMode !== 'workstation') {
      return;
    }
    if (chatRunBlocking) {
      notifyError('请先停止或完成当前回复');
      return;
    }
    if (!activeProjectId) {
      notifyError('请先选择 Workspace 和 Project');
      return;
    }
    if (hasImages) {
      notifyError('图片不会带入工作室');
    }
    const sessionId = activeSessionId || createId();
    if (!activeSessionId) {
      setActiveSessionId(sessionId);
    }
    setEntering(true);
    try {
      const existing = await fetchStudioByChat(sessionId);
      if (existing) {
        setExistingStudio(existing);
        openGroupStudio(existing.id);
        return;
      }
      const files = fileAttachments.length > 0
        ? await uploadFilesForSession(fileAttachments, activeProjectId, sessionId)
        : [];
      const filePaths = files.map((file) => file.path).filter(Boolean);
      // 切换入口创建空白工作室；只有当前输入框草稿或附件才作为明确的开场内容带入。
      if (draftText.trim() || filePaths.length > 0) {
        const studio = await createStudioFromChat({
          projectId: activeProjectId,
          chatSessionId: sessionId,
          agentId,
          modelConfigId,
          draftText,
          filePaths,
        });
        setExistingStudio(studio);
        openGroupStudio(studio.id);
        return;
      }
      openGroupStudio(createLocalStudioId(), { chatSessionId: sessionId, agentId, modelConfigId });
    } catch (error) {
      notifyError(getErrorMessage(error, '无法进入工作室'));
    } finally {
      setEntering(false);
    }
  }, [
    activeProjectId,
    activeSessionId,
    agentId,
    modelConfigId,
    busy,
    chatRunBlocking,
    draftText,
    fileAttachments,
    flagEnabled,
    hasImages,
    openGroupStudio,
    setActiveSessionId,
    uploadFilesForSession,
    workspaceMode,
  ]);

  const handleToggle = useCallback(() => {
    if (!flagEnabled || workspaceMode !== 'workstation') {
      return;
    }
    if (busy) {
      return;
    }
    if (inStudio) {
      closeGroupStudio();
      return;
    }
    void enterStudio();
  }, [busy, closeGroupStudio, enterStudio, flagEnabled, inStudio, workspaceMode]);

  useGroupStudioHotkey({
    enabled: flagEnabled,
    isComposingRef,
    onToggle: handleToggle,
  });

  const buttonLabel = inStudio
    ? '返回 Chat'
    : existingStudio
      ? '回到工作室'
      : '进入工作室';

  return {
    buttonLabel,
    entering,
    flagEnabled,
    handleToggle,
    inStudio,
    shortcutLabel,
  };
}
