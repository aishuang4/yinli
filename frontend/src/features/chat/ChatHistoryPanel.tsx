/** Chat 历史会话目录。列表已经按当前 Project 过滤。 */

import { PanelLeftClose, Plus, Search, Trash2 } from 'lucide-react';
import { Empty, Spinner, Tooltip } from '@/components/design';
import type { ChatSession } from './types';

interface ChatHistoryPanelProps {
  activeSessionId: string | null;
  collapsed: boolean;
  isMacDesktopShell: boolean;
  isStreaming: boolean;
  loading: boolean;
  projectName: string;
  sessions: ChatSession[];
  onCollapse: () => void;
  onDeleteSession: (session: ChatSession) => void;
  onNewSession: () => void;
  onSelectSession: (session: ChatSession) => void;
}

export function ChatHistoryPanel({
  activeSessionId,
  collapsed,
  isMacDesktopShell,
  isStreaming,
  loading,
  projectName,
  sessions,
  onCollapse,
  onDeleteSession,
  onNewSession,
  onSelectSession,
}: ChatHistoryPanelProps) {
  return (
    <aside
      className="chat-history-panel chat-workbench-history flex min-h-0 flex-col overflow-hidden"
      data-collapsed={collapsed}
      data-mac-shell={isMacDesktopShell}
    >
      {isMacDesktopShell && <div className="chat-workbench-electron-spacer" aria-hidden="true" />}
      <div className="chat-history-header px-4 pb-3 pt-3">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">历史对话</h2>
          <div className="chat-history-actions flex items-center gap-2">
            <Tooltip title="折叠历史对话" placement="bottom" mode="dark">
              <button
                type="button"
                className="icon-button chat-history-collapse-button"
                onClick={onCollapse}
                aria-label="折叠历史对话"
              >
                <PanelLeftClose size={17} strokeWidth={1.7} />
              </button>
            </Tooltip>
            <button
              type="button"
              className="icon-button chat-history-new-button"
              onClick={onNewSession}
              disabled={isStreaming}
              aria-label="新建会话"
              title="新建会话"
            >
              <Plus size={15} />
            </button>
          </div>
        </div>
        <div className="chat-workbench-search flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-[var(--color-text-tertiary)]">
          <Search size={14} />
          <span className="truncate">{projectName || '默认项目'}</span>
          <span className="ml-auto shrink-0">{sessions.length} 个会话</span>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {loading ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : sessions.length === 0 ? (
          <Empty type="noData" description="暂无历史对话" />
        ) : (
          <div className="space-y-1">
            {sessions.map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => onSelectSession(session)}
                className={`chat-session-item group w-full rounded-lg px-3 py-2 text-left transition-all duration-300 ease-out ${
                  activeSessionId === session.id
                    ? 'chat-session-item--active'
                    : 'hover:bg-[var(--color-bg-hover)]'
                }`}
              >
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                      {session.title}
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
                      <span className="truncate">{session.agent_name}</span>
                      <span>{session.message_count} 条</span>
                      {(session.groupStudio || session.group_studio) ? (
                        <span className="chat-session-studio-badge">工作室</span>
                      ) : null}
                    </div>
                  </div>
                  <span
                    role="button"
                    tabIndex={0}
                    className="rounded p-1 text-[var(--color-text-tertiary)] opacity-0 transition-all duration-300 ease-out hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-error)] group-hover:opacity-100"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDeleteSession(session);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        event.stopPropagation();
                        onDeleteSession(session);
                      }
                    }}
                    aria-label="删除会话"
                  >
                    <Trash2 size={14} />
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
