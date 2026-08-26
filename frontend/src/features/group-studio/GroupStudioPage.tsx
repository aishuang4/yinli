/** 群聊工作室独立界面骨架。不复用 Chat 气泡 / Composer / Team Blocks。 */

import { useEffect, useState } from 'react';
import { Users } from 'lucide-react';
import { Spinner } from '@/components/design';
import { useAppStore } from '../../stores/appStore';
import { fetchGroupStudio } from './api';
import { StudioTopbar } from './StudioTopbar';
import type { GroupStudioSnapshot } from './types';
import './GroupStudioPage.css';

const STATUS_LABEL: Record<string, string> = {
  drafting: '填写任务卡',
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
  const studioSurface = useAppStore((state) => state.studioSurface);
  const [studio, setStudio] = useState<GroupStudioSnapshot | null>(null);
  const [error, setError] = useState('');
  const isMac = window.electronAPI?.getPlatform() === 'darwin'
    || (!window.electronAPI && /Mac/i.test(navigator.platform));
  const shortcutLabel = isMac ? '⌘⇧G' : 'Ctrl+Shift+G';

  useEffect(() => {
    if (!groupId || studioSurface === 'none') {
      return;
    }
    let mounted = true;
    setError('');
    void fetchGroupStudio(groupId)
      .then((snapshot) => {
        if (mounted) {
          setStudio(snapshot);
        }
      })
      .catch((cause: unknown) => {
        if (mounted) {
          setError(cause instanceof Error ? cause.message : '无法加载工作室');
        }
      });
    return () => {
      mounted = false;
    };
  }, [groupId, studioSurface]);

  const members = (studio?.members ?? []).filter((member) => member.status !== 'removed');

  return (
    <div className="group-studio-shell">
      <StudioTopbar
        onBack={closeGroupStudio}
        shortcutLabel={shortcutLabel}
        title={studio?.title || '工作室'}
        statusLabel={studio ? (STATUS_LABEL[studio.status] || studio.status) : '加载中'}
      />
      {error ? (
        <div className="group-studio-error">{error}</div>
      ) : !studio && groupId ? (
        <div className="group-studio-loading">
          <Spinner />
          <span>正在打开工作室…</span>
        </div>
      ) : (
        <div className="group-studio-columns">
          <aside className="group-studio-pane group-studio-pane--members">
            <div className="group-studio-pane__label">成员</div>
            <ul className="group-studio-member-list">
              {members.map((member) => (
                <li key={member.id} data-role={member.role}>
                  <span className="group-studio-member-list__icon"><Users size={14} /></span>
                  <span className="min-w-0 truncate">
                    {member.displayName}
                    <span className="group-studio-member-list__role">
                      {member.role === 'host' ? '主持人' : '执行'}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </aside>
          <section className="group-studio-pane group-studio-pane--timeline">
            <div className="group-studio-pane__label">时间线</div>
            <p className="group-studio-placeholder">
              讨论与执行过程将显示在这里。当前版本只完成入口与布局，主持人尚未开始发言。
            </p>
          </section>
          <aside className="group-studio-pane group-studio-pane--board">
            <div className="group-studio-pane__label">任务卡</div>
            <p className="group-studio-goal">
              {studio?.taskCard.goal || '还没有目标。在右侧补全任务卡后才会开始讨论。'}
            </p>
            <div className="group-studio-pane__label" style={{ marginTop: 16 }}>黑板</div>
            <p className="group-studio-placeholder">共识决策会写在这里，不翻长聊天记录。</p>
          </aside>
        </div>
      )}
    </div>
  );
}
