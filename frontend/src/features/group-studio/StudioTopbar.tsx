/** 工作室顶栏：返回 Chat、预算、暂停/停止；复制 Chat 的 Electron spacer / drag。 */

import { ArrowLeft, Pause, Play, Square } from 'lucide-react';
import { Tooltip } from '@/components/design';

interface StudioTopbarProps {
  onBack: () => void;
  shortcutLabel: string;
  title: string;
  statusLabel: string;
  budgetLabel?: string;
  paused?: boolean;
  onPause?: () => void;
  onResume?: () => void;
  onInterrupt?: () => void;
  onClose?: () => void;
  closeLabel?: string;
}

export function StudioTopbar({
  onBack,
  shortcutLabel,
  title,
  statusLabel,
  budgetLabel,
  paused,
  onPause,
  onResume,
  onInterrupt,
  onClose,
  closeLabel,
}: StudioTopbarProps) {
  const isDesktopShell = Boolean(window.electronAPI);
  const desktopPlatform = window.electronAPI?.getPlatform();
  const isMacDesktopShell = desktopPlatform === 'darwin';
  const isWindowsDesktopShell = desktopPlatform === 'win32';

  return (
    <div
      className="group-studio-topbar-wrap"
      data-electron-shell={isDesktopShell}
      data-mac-shell={isMacDesktopShell}
      data-windows-shell={isWindowsDesktopShell}
    >
      {isMacDesktopShell && <div className="chat-workbench-electron-spacer" aria-hidden="true" />}
      <header className="group-studio-topbar">
        <div className="group-studio-topbar__left">
          <Tooltip title={`返回 Chat ${shortcutLabel}`} placement="bottom" mode="dark">
            <button
              type="button"
              className="icon-button group-studio-topbar__action"
              onClick={onBack}
              aria-label="返回 Chat"
            >
              <ArrowLeft size={16} strokeWidth={1.8} />
              <span>返回 Chat</span>
            </button>
          </Tooltip>
          <div className="group-studio-topbar__title min-w-0">
            <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{title}</div>
            <div className="truncate text-xs text-[var(--color-text-tertiary)]">
              {statusLabel}
              {budgetLabel ? ` · ${budgetLabel}` : ''}
            </div>
          </div>
        </div>
        <div className="group-studio-topbar__right">
          {paused ? (
            <button
              type="button"
              className="icon-button group-studio-topbar__action"
              onClick={onResume}
              aria-label="继续工作室"
            >
              <Play size={15} />
              <span>继续</span>
            </button>
          ) : (
            <button
              type="button"
              className="icon-button group-studio-topbar__ghost"
              onClick={onPause}
              disabled={!onPause}
              aria-label="暂停工作室"
            >
              <Pause size={15} />
              <span>暂停</span>
            </button>
          )}
          <button
            type="button"
            className="icon-button group-studio-topbar__ghost"
            onClick={onInterrupt}
            disabled={!onInterrupt}
            aria-label="停止当前发言"
          >
            <Square size={14} />
            <span>停止</span>
          </button>
          <button
            type="button"
            className="icon-button group-studio-topbar__ghost"
            onClick={onClose}
            disabled={!onClose}
            aria-label={closeLabel || '结束工作室'}
          >
            <Square size={14} />
            <span>{closeLabel || '结束'}</span>
          </button>
        </div>
      </header>
    </div>
  );
}
