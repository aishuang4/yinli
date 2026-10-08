/**
 * 群聊工作室三栏的折叠。只负责宽度和面板开关，不加载房间数据。
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

export const HISTORY_PANEL_TRANSITION_MS = 260;
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

export function useGroupStudioShell() {
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

  return {
    shellRef,
    isDesktopShell,
    isMacDesktopShell,
    isWindowsDesktopShell,
    shortcutLabel,
    historyPanelMounted,
    effectiveHistoryCollapsed,
    effectiveContextCollapsed,
    handleCollapseHistory,
    handleExpandHistory,
    handleToggleContext,
  };
}
