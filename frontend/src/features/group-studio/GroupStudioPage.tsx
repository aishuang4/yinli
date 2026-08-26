/** 群聊工作室独立界面。任务卡与加人可编辑；主持发言仍待后续 PR。 */

import { useEffect, useMemo, useState } from 'react';
import { Users } from 'lucide-react';
import { Button, Spinner } from '@/components/design';
import { SelectField, TextAreaField } from '../../components/form';
import { useAgents } from '../../hooks/useAgents';
import { useProjectResources } from '../../hooks/useProjectResources';
import { useAppStore } from '../../stores/appStore';
import { getErrorMessage, notifyError, notifySuccess } from '../../utils/feedback';
import { addStudioMember, fetchGroupStudio, updateTaskCard } from './api';
import { StudioTopbar } from './StudioTopbar';
import type { GroupStudioSnapshot } from './types';
import { useGroupStudioStream } from './useGroupStudioStream';
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
  const { agents } = useAgents();
  const [studio, setStudio] = useState<GroupStudioSnapshot | null>(null);
  const { enabledByType } = useProjectResources(studio?.projectId || '');
  const [error, setError] = useState('');
  const [goal, setGoal] = useState('');
  const [savingCard, setSavingCard] = useState(false);
  const [adding, setAdding] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState('default');
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
          setGoal(snapshot.taskCard.goal);
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

  const { messages, status: liveStatus } = useGroupStudioStream(
    studio?.id || groupId,
    Boolean(studio && studioSurface !== 'none'),
  );
  const members = (studio?.members ?? []).filter((member) => member.status !== 'removed');
  const timeline = messages.length > 0
    ? messages
    : (studio?.messages ?? []).filter((item) => item.visibleInTimeline !== false);
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
        label: agent.id === 'default' ? `${agent.name} · 默认助手` : agent.name,
        value: agent.id,
      }));
  }, [agents, enabledByType.agent, studio?.members]);

  const handleSaveGoal = async () => {
    if (!studio) {
      return;
    }
    setSavingCard(true);
    try {
      await updateTaskCard(studio.id, { projectId: studio.projectId, goal });
      const snapshot = await fetchGroupStudio(studio.id);
      setStudio(snapshot);
      setGoal(snapshot.taskCard.goal);
      notifySuccess(snapshot.status === 'discussing' ? '任务卡已生效，可以开始讨论。' : '任务卡已保存。');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法更新任务卡'));
    } finally {
      setSavingCard(false);
    }
  };

  const handleAddMember = async () => {
    if (!studio || !selectedAgentId) {
      return;
    }
    setAdding(true);
    try {
      await addStudioMember(studio.id, { projectId: studio.projectId, agentId: selectedAgentId });
      const snapshot = await fetchGroupStudio(studio.id);
      setStudio(snapshot);
      notifySuccess('已加入工作室');
    } catch (cause) {
      notifyError(getErrorMessage(cause, '无法加人'));
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="group-studio-shell">
      <StudioTopbar
        onBack={closeGroupStudio}
        shortcutLabel={shortcutLabel}
        title={studio?.title || '工作室'}
        statusLabel={studio ? (STATUS_LABEL[liveStatus || studio.status] || liveStatus || studio.status) : '加载中'}
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
            <div className="group-studio-add-member">
              <SelectField
                label="加人"
                value={selectedAgentId}
                options={memberOptions}
                onChange={(value) => setSelectedAgentId(String(value))}
              />
              <Button
                size="small"
                loading={adding}
                disabled={adding || memberOptions.length === 0}
                onClick={() => void handleAddMember()}
              >
                加入
              </Button>
            </div>
          </aside>
          <section className="group-studio-pane group-studio-pane--timeline">
            <div className="group-studio-pane__label">时间线</div>
            {timeline.length === 0 ? (
              <p className="group-studio-placeholder">
                {studio?.status === 'drafting'
                  ? '先在右侧填写目标。没有目标时主持人不发言。'
                  : '主持人正在准备发言，或还没有消息。'}
              </p>
            ) : (
              <ol className="group-studio-timeline">
                {timeline.map((item) => (
                  <li key={item.id} data-role={item.role}>
                    <div className="group-studio-timeline__meta">
                      {item.role === 'host' ? '主持人' : item.role}
                    </div>
                    <div className="group-studio-timeline__body">{item.body || '…'}</div>
                  </li>
                ))}
              </ol>
            )}
          </section>
          <aside className="group-studio-pane group-studio-pane--board">
            <div className="group-studio-pane__label">任务卡</div>
            <TextAreaField
              label="目标"
              required
              rows={5}
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder="这间工作室要完成什么？"
            />
            <Button
              size="small"
              loading={savingCard}
              disabled={savingCard || !studio}
              onClick={() => void handleSaveGoal()}
            >
              保存任务卡
            </Button>
            <div className="group-studio-pane__label" style={{ marginTop: 20 }}>黑板</div>
            <p className="group-studio-placeholder">共识决策会写在这里，不翻长聊天记录。</p>
          </aside>
        </div>
      )}
    </div>
  );
}
