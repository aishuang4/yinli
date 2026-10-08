/**
 * 群聊工作室顶栏：返回、列表开关、模型和设置。
 */

import { PanelLeftOpen, PanelRightClose, PanelRightOpen, Plus, Settings, Users } from 'lucide-react';
import { Tooltip } from '@/components/design';
import { NotificationBell } from '../notifications/NotificationBell';
import { ModelSelector } from '../models/ModelSelector';
import type { ModelCatalog } from '../models/useModelCatalog';

interface StudioTopBarProps {
  isMacDesktopShell: boolean;
  historyCollapsed: boolean;
  historyMounted: boolean;
  shortcutLabel: string;
  title: string;
  statusText: string;
  budgetText: string;
  contextCollapsed: boolean;
  catalog: ModelCatalog | null;
  modelValue: string;
  modelDisabled: boolean;
  modelsLoading: boolean;
  modelsError: string;
  newDisabled: boolean;
  onExpandHistory: () => void;
  onNewStudio: () => void;
  onClose: () => void;
  onToggleContext: () => void;
  onModelChange: (value: string) => void;
  onOpenSettings: () => void;
}

export function StudioTopBar({
  isMacDesktopShell,
  historyCollapsed,
  historyMounted,
  shortcutLabel,
  title,
  statusText,
  budgetText,
  contextCollapsed,
  catalog,
  modelValue,
  modelDisabled,
  modelsLoading,
  modelsError,
  newDisabled,
  onExpandHistory,
  onNewStudio,
  onClose,
  onToggleContext,
  onModelChange,
  onOpenSettings,
}: StudioTopBarProps) {
  return (
    <header
      className="chat-workbench-topbar flex items-center justify-between gap-4 px-8 py-2"
      style={isMacDesktopShell && (!historyMounted || historyCollapsed) ? { paddingLeft: 128 } : undefined}
    >
      <div className="chat-workbench-title-group flex min-w-0 items-center gap-3">
        {historyCollapsed ? (
          <div className="flex shrink-0 items-center gap-2">
            <Tooltip title="展开群聊列表" placement="bottom" mode="dark">
              <button
                type="button"
                className="icon-button chat-history-collapse-button"
                onClick={onExpandHistory}
                aria-label="展开群聊列表"
              >
                <PanelLeftOpen size={17} strokeWidth={1.7} />
              </button>
            </Tooltip>
            <Tooltip title="新建群聊" placement="bottom" mode="dark">
              <button
                type="button"
                className="icon-button chat-history-new-button"
                onClick={onNewStudio}
                disabled={newDisabled}
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
            onClick={onClose}
            aria-label="返回 Chat"
          >
            <Users size={17} strokeWidth={1.7} />
          </button>
        </Tooltip>
        <div className="chat-workbench-title-copy min-w-0">
          <div className="truncate text-base font-semibold text-[var(--color-text-primary)]">
            {title}
          </div>
          <div className="truncate text-xs text-[var(--color-text-tertiary)]">
            {statusText}
            {budgetText}
          </div>
        </div>
      </div>
      <div className="chat-workbench-controls flex min-w-0 shrink items-center gap-2">
        <Tooltip title={contextCollapsed ? '展开成员与备注' : '折叠成员与备注'} placement="bottom" mode="dark">
          <button
            type="button"
            className="icon-button chat-workbench-top-action h-9 w-9 rounded-xl"
            onClick={onToggleContext}
            aria-label={contextCollapsed ? '展开成员与备注' : '折叠成员与备注'}
          >
            {contextCollapsed ? <PanelRightOpen size={17} /> : <PanelRightClose size={17} />}
          </button>
        </Tooltip>
        <NotificationBell />
        <div className="chat-workbench-model-control min-w-0 shrink">
          <ModelSelector
            catalog={catalog}
            value={modelValue}
            onChange={onModelChange}
            disabled={modelDisabled}
            loading={modelsLoading}
            error={modelsError}
          />
        </div>
        <Tooltip title="工作台设置" placement="bottom" mode="dark">
          <button
            type="button"
            className="icon-button chat-workbench-top-action h-9 w-9 rounded-xl"
            onClick={onOpenSettings}
            aria-label="进入工作台设置"
          >
            <Settings size={17} />
          </button>
        </Tooltip>
      </div>
    </header>
  );
}
