/**
 * 时间线原文。按工作区、项目、智能体、场合和时间筛选。
 */

import { useEffect, useState } from 'react';
import { Empty, Spinner } from '@/components/design';
import { DateField, SelectField, TextField } from '@/components/form';
import { useProjects } from '../../hooks/useProjects';
import type { Agent } from '../../types/agent';
import type { TimelineMessage, TimelineQueryResult } from '../../types/memory';

interface TimelineMemoryPanelProps {
  agents: Agent[];
  loadTimeline: (filters: {
    projectSlug?: string;
    agentId?: string;
    surface?: string;
    timeFrom?: string;
    timeTo?: string;
    queryText?: string;
  }) => Promise<TimelineQueryResult>;
}

export function TimelineMemoryPanel({ agents, loadTimeline }: TimelineMemoryPanelProps) {
  const { activeProjects, activeProjectSlug } = useProjects();
  const [projectOverride, setProjectOverride] = useState<string | null>(null);
  const [agentId, setAgentId] = useState('');
  const [surface, setSurface] = useState('');
  const [queryText, setQueryText] = useState('');
  const [timeFrom, setTimeFrom] = useState('');
  const [timeTo, setTimeTo] = useState('');
  const [messages, setMessages] = useState<TimelineMessage[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [openId, setOpenId] = useState('');

  const projectSlug = projectOverride === null ? activeProjectSlug : projectOverride;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void loadTimeline({
      projectSlug,
      agentId,
      surface,
      queryText,
      timeFrom,
      timeTo: timeTo ? `${timeTo}T23:59:59.999999` : '',
    }).then((next) => {
      if (cancelled) return;
      setMessages(next.messages || []);
      setTruncated(Boolean(next.truncated));
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [projectSlug, agentId, surface, queryText, timeFrom, timeTo, loadTimeline]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <SelectField
          value={projectSlug}
          onChange={(value) => setProjectOverride(String(value ?? ''))}
          options={[
            { label: '全部项目', value: '' },
            ...activeProjects.map((item) => ({ label: item.name || item.slug, value: item.slug })),
          ]}
        />
        <SelectField
          value={surface}
          onChange={(value) => setSurface(String(value ?? ''))}
          options={[
            { label: '全部场合', value: '' },
            { label: '对话', value: 'chat' },
            { label: '群聊', value: 'group' },
            { label: '定时任务', value: 'job' },
          ]}
        />
        <SelectField
          value={agentId}
          onChange={(value) => setAgentId(String(value ?? ''))}
          options={[
            { label: '全部智能体', value: '' },
            ...agents.map((agent) => ({ label: agent.name || agent.id, value: agent.id })),
          ]}
        />
        <DateField placeholder="开始时间" value={timeFrom} onChange={setTimeFrom} />
        <DateField placeholder="结束时间" value={timeTo} onChange={setTimeTo} />
        <TextField
          className="lg:col-span-2"
          placeholder="关键词"
          value={queryText}
          onChange={(event) => setQueryText(event.target.value)}
          allowClear
        />
      </div>
      {truncated ? <p className="text-xs text-[var(--color-text-tertiary)]">结果已按长度截断，可以缩小时间或标签。</p> : null}
      <div className="min-h-0 flex-1 overflow-auto">
        {loading ? <Spinner /> : null}
        {messages.length === 0 && !loading ? <Empty description="这条时间线上还没有对得上的原文" /> : null}
        {messages.map((item) => (
          <button
            key={item.id}
            type="button"
            className="block w-full border-b px-2 py-2 text-left"
            onClick={() => setOpenId(openId === item.id ? '' : item.id)}
          >
            <span className="text-xs text-[var(--color-text-tertiary)]">
              {item.createdAt} · {item.surface} · {item.agentName || item.agentId || '未知'} · {item.role}
            </span>
            <span className="mt-1 block whitespace-pre-wrap text-sm">
              {openId === item.id ? item.content : (item.content || '').slice(0, 160)}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
