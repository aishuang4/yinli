/**
 * MemorySettingsPanel — 记忆设置快照和审计详情。
 */

import { useEffect, useMemo, useState } from 'react';
import { ClipboardList, Database, FileText, RefreshCw, Settings2, ThumbsDown, ThumbsUp } from 'lucide-react';
import { Button, Empty, Spinner, Tag } from '@/components/design';
import type {
  MemoryAdmissionPolicyUpdate,
  MemoryAuditEvent,
  MemoryRetrievalFeedbackPayload,
  MemoryRetrievalFeedbackRating,
  MemorySettingsSnapshot,
} from '../../types/memory';
import { DetailSection, MetricBlock } from './MemoryDetailBlocks';
import { formatDateTime } from './memoryViewConfig';

interface MemorySettingsPanelProps {
  settings: MemorySettingsSnapshot | null;
  auditEvents: MemoryAuditEvent[];
  loading: boolean;
  onRefresh: () => Promise<void>;
  onSaveAdmission: (payload: MemoryAdmissionPolicyUpdate) => Promise<void>;
  onRetrievalFeedback: (payload: MemoryRetrievalFeedbackPayload) => Promise<void>;
}

export function MemorySettingsPanel({
  settings,
  auditEvents,
  loading,
  onRefresh,
  onSaveAdmission,
  onRetrievalFeedback,
}: MemorySettingsPanelProps) {
  const [draft, setDraft] = useState<MemoryAdmissionPolicyUpdate>({});
  const [saving, setSaving] = useState(false);
  const [feedbackKey, setFeedbackKey] = useState('');
  const retrievalSummary = useMemo(() => summarizeRetrievalAudit(auditEvents), [auditEvents]);

  useEffect(() => {
    if (settings?.admission) {
      setDraft(settings.admission);
    }
  }, [settings]);

  async function saveAdmission() {
    setSaving(true);
    try {
      await onSaveAdmission(draft);
    } finally {
      setSaving(false);
    }
  }

  const updateDraft = (key: keyof MemoryAdmissionPolicyUpdate, value: number | boolean) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  async function submitFeedback(
    event: MemoryAuditEvent,
    nodeId: string,
    rating: MemoryRetrievalFeedbackRating,
  ) {
    const key = `${event.id}:${nodeId}:${rating}`;
    setFeedbackKey(key);
    try {
      await onRetrievalFeedback({
        auditEventId: event.id,
        nodeId,
        rating,
        sessionId: event.sessionId,
        agentId: event.agentId || '',
        query: String(event.payload?.query || ''),
      });
    } finally {
      setFeedbackKey('');
    }
  }

  return (
    <section className="grid h-full min-h-0 grid-rows-[auto_1fr] overflow-hidden p-5">
      <div className="flex items-start justify-between gap-4 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
            <Settings2 size={16} color="var(--color-accent)" />
            设置与审计
          </div>
          <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">
            管理准入阈值、模型评分、Markdown 质量策略，并查看 MEMORY_AUDIT 事件。
          </p>
        </div>
        <Button icon={<RefreshCw size={14} />} onClick={() => void onRefresh()} loading={loading}>
          刷新
        </Button>
      </div>

      <div className="mt-5 grid min-h-0 grid-cols-[360px_minmax(0,1fr)] gap-5 overflow-hidden">
        <div className="min-h-0 overflow-y-auto rounded-md border border-[var(--color-border)]">
          <div className="border-b border-[var(--color-border)] px-4 py-3 text-sm font-medium text-[var(--color-text-primary)]">
            设置快照
          </div>
          {loading && !settings ? (
            <div className="flex justify-center py-12">
              <Spinner tip="加载 Memory 设置..." />
            </div>
          ) : !settings ? (
            <div className="p-6">
              <Empty type="noData" description="暂无设置快照" />
            </div>
          ) : (
            <div className="space-y-4 p-4">
              <div className="grid grid-cols-2 gap-3">
                <MetricBlock label="向量库" value={settings.vectorStore.provider} />
                <MetricBlock label="召回等级" value={String(settings.vectorStore.retrievalLevel)} />
              </div>

              <DetailSection title="向量策略" icon={<Database size={14} />}>
                <div className="space-y-1 text-xs">
                  <div>Embedding：{settings.vectorStore.embeddingProvider || 'disabled'}</div>
                  <div>模型：{settings.vectorStore.embeddingModel || '未配置'}</div>
                  <div>维度：{settings.vectorStore.embeddingDimensions || '无'}</div>
                  <div>Schema：v{settings.vectorStore.schemaVersion}</div>
                </div>
              </DetailSection>

              <DetailSection title="召回质量" icon={<Database size={14} />}>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <MetricBlock label="召回次数" value={String(retrievalSummary.total)} />
                  <MetricBlock label="空结果率" value={`${retrievalSummary.emptyRate}%`} />
                  <MetricBlock label="平均分" value={retrievalSummary.averageTopScore} />
                </div>
                <div className="mt-3 flex flex-wrap gap-1">
                  {retrievalSummary.sources.length === 0 ? (
                    <Tag size="small" color="default">暂无来源</Tag>
                  ) : retrievalSummary.sources.map((source) => (
                    <Tag key={source} size="small" color="blue">{source}</Tag>
                  ))}
                </div>
                <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                  基于最近 {auditEvents.length} 条 MEMORY_AUDIT 中的召回事件统计。
                </div>
              </DetailSection>

              <DetailSection title="准入质量" icon={<Settings2 size={14} />}>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <MetricBlock label="历史阈值" value={settings.admission.historyThreshold.toFixed(2)} />
                  <MetricBlock label="Markdown" value={settings.admission.markdownThreshold.toFixed(2)} />
                  <MetricBlock label="Skill" value={settings.admission.skillThreshold.toFixed(2)} />
                </div>
                <div className="mt-3 flex flex-wrap gap-1">
                  <Tag size="small" color="blue">{settings.admission.mode}</Tag>
                  <Tag size="small" color={settings.admission.rejectSensitive ? 'success' : 'mauve'}>
                    敏感信息拒绝
                  </Tag>
                  <Tag size="small" color={settings.admission.enableRedaction ? 'success' : 'mauve'}>
                    审计脱敏
                  </Tag>
                  <Tag size="small" color={settings.admission.enableModelScoring ? 'success' : 'default'}>
                    模型评分
                  </Tag>
                </div>
                <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                  最小长度：用户 {settings.admission.minUserChars} / Agent {settings.admission.minAssistantChars}
                </div>
                <div className="mt-4 space-y-3 border-t border-[var(--color-border)] pt-3">
                  <RangeField
                    label="历史记忆阈值"
                    value={draft.historyThreshold ?? settings.admission.historyThreshold}
                    onChange={(value) => updateDraft('historyThreshold', value)}
                  />
                  <RangeField
                    label="Markdown 阈值"
                    value={draft.markdownThreshold ?? settings.admission.markdownThreshold}
                    onChange={(value) => updateDraft('markdownThreshold', value)}
                  />
                  <RangeField
                    label="Skill 阈值"
                    value={draft.skillThreshold ?? settings.admission.skillThreshold}
                    onChange={(value) => updateDraft('skillThreshold', value)}
                  />
                  <RangeField
                    label="模型评分权重"
                    value={draft.modelScoreWeight ?? settings.admission.modelScoreWeight}
                    onChange={(value) => updateDraft('modelScoreWeight', value)}
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <NumberField
                      label="摘要每节条数"
                      value={draft.summaryMaxItemsPerSection ?? settings.admission.summaryMaxItemsPerSection}
                      min={1}
                      max={50}
                      onChange={(value) => updateDraft('summaryMaxItemsPerSection', value)}
                    />
                    <NumberField
                      label="写入重试"
                      value={draft.markdownWriteRetries ?? settings.admission.markdownWriteRetries}
                      min={0}
                      max={5}
                      onChange={(value) => updateDraft('markdownWriteRetries', value)}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <CheckboxField
                      label="启用模型评分"
                      checked={Boolean(draft.enableModelScoring ?? settings.admission.enableModelScoring)}
                      onChange={(value) => updateDraft('enableModelScoring', value)}
                    />
                    <CheckboxField
                      label="敏感信息拒绝"
                      checked={Boolean(draft.rejectSensitive ?? settings.admission.rejectSensitive)}
                      onChange={(value) => updateDraft('rejectSensitive', value)}
                    />
                    <CheckboxField
                      label="审计脱敏"
                      checked={Boolean(draft.enableRedaction ?? settings.admission.enableRedaction)}
                      onChange={(value) => updateDraft('enableRedaction', value)}
                    />
                  </div>
                  <Button size="small" onClick={() => void saveAdmission()} loading={saving}>
                    保存准入策略
                  </Button>
                </div>
              </DetailSection>

              <DetailSection title="Markdown 路径" icon={<FileText size={14} />}>
                <div className="space-y-2">
                  {Object.entries(settings.markdownPaths).map(([key, value]) => (
                    <div key={key}>
                      <div className="text-xs text-[var(--color-text-tertiary)]">{key}</div>
                      <div className="break-all font-mono text-xs">{value}</div>
                    </div>
                  ))}
                </div>
              </DetailSection>

              <DetailSection title="运行时边界" icon={<Settings2 size={14} />}>
                <div className="flex flex-wrap gap-1">
                  <Tag size="small" color={settings.runtime.usesHooksAndTools ? 'success' : 'default'}>
                    hooks/tools
                  </Tag>
                  <Tag size="small" color={settings.runtime.memoryRuntimeKind ? 'mauve' : 'success'}>
                    无 memory runtime.kind
                  </Tag>
                  <Tag size="small" color={settings.runtime.chatShowsMemoryProcess ? 'mauve' : 'success'}>
                    Chat 不展示记忆过程
                  </Tag>
                  <Tag size="small" color="blue">Wiki {settings.wiki.graphStatus}</Tag>
                </div>
              </DetailSection>
            </div>
          )}
        </div>

        <div className="min-h-0 overflow-y-auto rounded-md border border-[var(--color-border)]">
          <div className="border-b border-[var(--color-border)] px-4 py-3 text-sm font-medium text-[var(--color-text-primary)]">
            审计详情
          </div>
          {loading && auditEvents.length === 0 ? (
            <div className="flex justify-center py-12">
              <Spinner tip="加载 Memory 审计..." />
            </div>
          ) : auditEvents.length === 0 ? (
            <div className="p-6">
              <Empty type="noData" description="暂无 MEMORY_AUDIT 事件" />
            </div>
          ) : (
            <div className="divide-y divide-[var(--color-border)]">
              {auditEvents.map((event) => (
                <article key={event.id} className="space-y-3 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-[var(--color-text-primary)]">
                        {event.title || String(event.payload?.memoryType || 'Memory Audit')}
                      </div>
                      <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                        {formatDateTime(event.createdAt)} · {event.sessionId}
                      </div>
                    </div>
                    <Tag size="small" color={event.status === 'failed' ? 'mauve' : 'blue'}>{event.status || 'unknown'}</Tag>
                  </div>
                  {event.content && (
                    <p className="text-xs leading-5 text-[var(--color-text-secondary)]">{event.content}</p>
                  )}
                  <RetrievalFeedbackBlock
                    event={event}
                    loadingKey={feedbackKey}
                    onFeedback={(nodeId, rating) => void submitFeedback(event, nodeId, rating)}
                  />
                  <DetailSection title="Payload" icon={<ClipboardList size={14} />}>
                    <pre className="max-h-[220px] overflow-auto rounded-md bg-[var(--color-bg-primary)] p-2 text-xs leading-5">
                      {JSON.stringify(event.payload || {}, null, 2)}
                    </pre>
                  </DetailSection>
                </article>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function summarizeRetrievalAudit(events: MemoryAuditEvent[]) {
  const qualities = events
    .map((event) => toRecord(event.payload?.retrievalQuality))
    .filter((quality) => Object.keys(quality).length > 0);
  const total = qualities.length;
  const emptyCount = qualities.filter((quality) => quality.empty === true).length;
  const topScores = qualities
    .map((quality) => Number(quality.topScore))
    .filter((value) => Number.isFinite(value));
  const sources = Array.from(new Set(
    qualities.flatMap((quality) => Array.isArray(quality.retrievalSources)
      ? quality.retrievalSources.map((source) => String(source))
      : []),
  )).sort();
  const averageTopScore = topScores.length
    ? (topScores.reduce((sum, value) => sum + value, 0) / topScores.length).toFixed(2)
    : '0.00';

  return {
    total,
    emptyRate: total ? Math.round((emptyCount / total) * 100) : 0,
    averageTopScore,
    sources,
  };
}

function RetrievalFeedbackBlock({
  event,
  loadingKey,
  onFeedback,
}: {
  event: MemoryAuditEvent;
  loadingKey: string;
  onFeedback: (nodeId: string, rating: MemoryRetrievalFeedbackRating) => void;
}) {
  const quality = toRecord(event.payload?.retrievalQuality);
  const results = Array.isArray(quality.resultQuality)
    ? quality.resultQuality.map(toRecord).filter((item) => item.nodeId)
    : [];
  if (results.length === 0) return null;

  return (
    <DetailSection title="召回反馈" icon={<Database size={14} />}>
      <div className="space-y-2">
        {results.slice(0, 5).map((item) => {
          const nodeId = String(item.nodeId || '');
          return (
            <div
              key={`${event.id}-${nodeId}`}
              className="flex items-center justify-between gap-3 rounded-md bg-[var(--color-bg-primary)] px-3 py-2"
            >
              <div className="min-w-0">
                <div className="truncate font-mono text-xs text-[var(--color-text-primary)]">{nodeId}</div>
                <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                  rank {formatNumber(item.rankScore)} · vector {formatNumber(item.vectorScore)} · feedback {formatNumber(item.feedbackScore)}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  size="small"
                  icon={<ThumbsUp size={13} />}
                  loading={loadingKey === `${event.id}:${nodeId}:relevant`}
                  onClick={() => onFeedback(nodeId, 'relevant')}
                />
                <Button
                  size="small"
                  icon={<ThumbsDown size={13} />}
                  loading={loadingKey === `${event.id}:${nodeId}:irrelevant`}
                  onClick={() => onFeedback(nodeId, 'irrelevant')}
                />
              </div>
            </div>
          );
        })}
      </div>
    </DetailSection>
  );
}

function formatNumber(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : '0.00';
}

function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function RangeField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs text-[var(--color-text-secondary)]">
      <span className="mb-1 flex items-center justify-between">
        <span>{label}</span>
        <span className="font-mono">{Number(value).toFixed(2)}</span>
      </span>
      <input
        className="w-full accent-[var(--color-accent)]"
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs text-[var(--color-text-secondary)]">
      <span className="mb-1 block">{label}</span>
      <input
        className="h-8 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-2 text-xs text-[var(--color-text-primary)]"
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function CheckboxField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
      <input
        className="accent-[var(--color-accent)]"
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}
