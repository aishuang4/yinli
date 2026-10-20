/** Chat 进入/返回工作室：探活已有房间；没有则只打开空白画布，第一条消息才 from-chat。 */

import { useCallback, useEffect, useState, type RefObject } from 'react';
import { useAppStore } from '../../stores/appStore';
import { isSurfaceEnabled } from '../../stores/productFeatures';
import { useProductFeaturesStore } from '../../stores/productFeaturesStore';
import { createId } from '../../utils/id';
import { getErrorMessage, notifyError } from '../../utils/feedback';
import type { ChatFileAttachment } from '../chat/types';
import { fetchGroupStudioSettings, fetchStudioByChat } from './api';
import type { GroupStudioSnapshot } from './types';
import { useGroupStudioHotkey } from './useGroupStudioHotkey';

let pendingComposerPrefill = '';

export function takeStudioComposerPrefill(): string {
  const value = pendingComposerPrefill;
  pendingComposerPrefill = '';
  return value;
}

interface UseGroupStudioEntryOptions {
  activeSessionId: string | null;
  agentId: string;
  modelConfigId?: string;
  chatRunBlocking: boolean;
  draftText: string;
  lastUserMessage: string;
  fileAttachments: ChatFileAttachment[];
  hasImages: boolean;
  isComposingRef: RefObject<boolean>;
  setActiveSessionId: (sessionId: string) => void;
  uploadFilesForSession: (
    files: ChatFileAttachment[],
    sessionId: string,
  ) => Promise<Array<{ path: string }>>;
}

export function useGroupStudioEntry({
  activeSessionId,
  agentId,
  modelConfigId,
  chatRunBlocking,
  draftText,
  lastUserMessage,
  hasImages,
  isComposingRef,
  setActiveSessionId,
}: UseGroupStudioEntryOptions) {
  const workspaceMode = useAppStore((state) => state.workspaceMode);
  const studioSurface = useAppStore((state) => state.studioSurface);
  const studioTransition = useAppStore((state) => state.studioTransition);
  const openGroupStudio = useAppStore((state) => state.openGroupStudio);
  const closeGroupStudio = useAppStore((state) => state.closeGroupStudio);
  const productEnabled = useProductFeaturesStore((state) => isSurfaceEnabled(state.features, 'groupStudio'));
  const [flagEnabled, setFlagEnabled] = useState(false);
  const [existingStudio, setExistingStudio] = useState<GroupStudioSnapshot | null>(null);
  const [entering, setEntering] = useState(false);
  const isMac = window.electronAPI?.getPlatform() === 'darwin'
    || (!window.electronAPI && /Mac/i.test(navigator.platform));
  const shortcutLabel = isMac ? '⌘⇧G' : 'Ctrl+Shift+G';
  const enabled = flagEnabled && productEnabled;
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
    if (!enabled || !activeSessionId) {
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
  }, [activeSessionId, enabled, studioSurface]);

  const enterStudio = useCallback(async () => {
    if (!enabled || busy || workspaceMode !== 'workstation') {
      return;
    }
    if (chatRunBlocking) {
      notifyError('请先停止或完成当前回复');
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
        pendingComposerPrefill = draftText.trim();
        openGroupStudio(existing.id);
        return;
      }
      pendingComposerPrefill = draftText.trim() || lastUserMessage.trim();
      openGroupStudio(null, {
        chatSessionId: sessionId,
        agentId,
        modelConfigId,
        nonce: createId(),
      });
    } catch (error) {
      notifyError(getErrorMessage(error, '无法进入工作室'));
    } finally {
      setEntering(false);
    }
  }, [
    activeSessionId,
    agentId,
    modelConfigId,
    busy,
    chatRunBlocking,
    draftText,
    enabled,
    hasImages,
    lastUserMessage,
    openGroupStudio,
    setActiveSessionId,
    workspaceMode,
  ]);

  const handleToggle = useCallback(() => {
    if (!enabled || workspaceMode !== 'workstation') {
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
  }, [busy, closeGroupStudio, enterStudio, enabled, inStudio, workspaceMode]);

  useGroupStudioHotkey({
    enabled,
    isComposingRef,
    onToggle: handleToggle,
  });

  const buttonLabel = inStudio
    ? '返回 Chat'
    : existingStudio
      ? '回到群聊'
      : '进入群聊';

  return {
    buttonLabel,
    entering,
    flagEnabled: enabled,
    handleToggle,
    inStudio,
    shortcutLabel,
  };
}
