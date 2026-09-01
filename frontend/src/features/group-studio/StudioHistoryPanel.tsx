/** 群聊工作室独立历史列表，不展示 Chat 会话。 */

import { PanelLeftClose, Plus, Search, Users } from 'lucide-react';
import { Empty, Spinner, Tooltip } from '@/components/design';
import type { GroupStudioListItem } from './types';

interface StudioHistoryPanelProps {
  activeGroupId: string | null;
  collapsed: boolean;
  isMacDesktopShell: boolean;
  loading: boolean;
  studios: GroupStudioListItem[];
  statusLabel: (status: string) => string;
  onCollapse: () => void;
  onNewStudio: () => void;
  onSelectStudio: (studio: GroupStudioListItem) => void;
}

export function StudioHistoryPanel({
  activeGroupId,
  collapsed,
  isMacDesktopShell,
  loading,
  studios,
  statusLabel,
  onCollapse,
  onNewStudio,
  onSelectStudio,
}: StudioHistoryPanelProps) {
  return (
    <aside
      className="chat-history-panel chat-workbench-history flex min-h-0 flex-col overflow-hidden"
      data-collapsed={collapsed}
      data-mac-shell={isMacDesktopShell}
    >
      {isMacDesktopShell && <div className="chat-workbench-electron-spacer" aria-hidden="true" />}
      <div className="chat-history-header px-4 pb-3 pt-3">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">群聊工作室</h2>
          <div className="chat-history-actions flex items-center gap-2">
            <Tooltip title="折叠群聊列表" placement="bottom" mode="dark">
              <button
                type="button"
                className="icon-button chat-history-collapse-button"
                onClick={onCollapse}
                aria-label="折叠群聊列表"
              >
                <PanelLeftClose size={17} strokeWidth={1.7} />
              </button>
            </Tooltip>
            <button
              type="button"
              className="icon-button chat-history-new-button"
              onClick={onNewStudio}
              aria-label="新建群聊"
              title="新建群聊"
            >
              <Plus size={15} />
            </button>
          </div>
        </div>
        <div className="chat-workbench-search flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-[var(--color-text-tertiary)]">
          <Search size={14} />
          <span>{studios.length} 个群聊</span>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {loading ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : studios.length === 0 ? (
          <Empty type="noData" description="还没有群聊" />
        ) : (
          <div className="space-y-1">
            {studios.map((studio) => (
              <button
                key={studio.id}
                type="button"
                onClick={() => onSelectStudio(studio)}
                className={`chat-session-item group w-full rounded-lg px-3 py-2 text-left transition-all duration-300 ease-out ${
                  activeGroupId === studio.id
                    ? 'chat-session-item--active'
                    : 'hover:bg-[var(--color-bg-hover)]'
                }`}
              >
                <div className="flex items-start gap-2">
                  <Users size={14} className="mt-0.5 shrink-0 text-[var(--color-text-tertiary)]" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                      {studio.title || '未命名工作室'}
                    </div>
                    <div className="mt-1 truncate text-xs text-[var(--color-text-tertiary)]">
                      {statusLabel(studio.status)}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
