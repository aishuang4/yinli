/**
 * TraceDetailView — 第一层墙钟时间线，第二层只看选中 Observation 的 input/output。
 */

import { useEffect, useMemo, useState } from 'react';
import { Tag } from '@/components/design';
import type { TraceDetail, TraceObservation } from './traceTypes';
import { TraceEventTimeline } from './TraceEventTimeline';
import { TraceStatus } from './TraceStatus';
import { ObservationPayload } from './ObservationPayload';
import { formatDuration, formatJsonPreview, formatTraceTime, observationKindLabel } from './traceUtils';

interface TraceDetailViewProps {
  trace: TraceDetail;
}

export function TraceDetailView({ trace }: TraceDetailViewProps) {
  const root = useMemo(
    () => trace.observations.find((item) => item.kind === 'run') || trace.observations[0] || null,
    [trace.observations],
  );
  const [selectedId, setSelectedId] = useState(root?.id || '');

  useEffect(() => {
    if (!trace.observations.some((item) => item.id === selectedId)) {
      setSelectedId(root?.id || '');
    }
  }, [root?.id, selectedId, trace.observations]);

  const selected = trace.observations.find((item) => item.id === selectedId) || root;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-medium text-[var(--color-text-primary)]">{trace.title}</h2>
            <div className="mt-1 text-sm text-[var(--color-text-secondary)]">
              {trace.mode === 'team' ? `${trace.agentName || 'Agent'} · Team Leader` : trace.agentName}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <TraceStatus status={trace.status} />
            <Tag size="small" color="transparent">{formatDuration(trace.durationMs)}</Tag>
            <Tag size="small" color="transparent">{trace.observations.length} 步</Tag>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 pb-5">
        <TraceEventTimeline
          trace={trace}
          selectedId={selected?.id || ''}
          onSelect={(observation) => setSelectedId(observation.id)}
        />
        {selected ? <ObservationDetail observation={selected} /> : null}
      </div>
    </div>
  );
}

function ObservationDetail({ observation }: { observation: TraceObservation }) {
  return (
    <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-[var(--color-text-primary)]">
            {observationKindLabel(observation.kind)} · {observation.name}
          </div>
          <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {formatTraceTime(observation.startedAt)}
            {observation.endedAt ? ` → ${formatTraceTime(observation.endedAt)}` : ' → 进行中'}
            {' · '}
            {formatDuration(observation.durationMs)}
          </div>
        </div>
        <Tag size="small" color={observation.status === 'failed' ? 'red' : observation.status.startsWith('waiting') ? 'orange' : 'transparent'}>
          {observation.status}
        </Tag>
      </div>
      <ObservationPayload
        kind={observation.kind}
        input={observation.input}
        output={observation.output}
        metadata={observation.metadata}
      />
      {observation.kind !== 'generation' && Object.keys(observation.metadata || {}).length > 0 && (
        <div className="mt-3">
          <JsonPanel title="Metadata" value={observation.metadata} />
        </div>
      )}
    </section>
  );
}

function JsonPanel({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">{title}</div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[var(--color-bg-primary)] p-3 text-[11px] text-[var(--color-text-tertiary)]">
        {formatJsonPreview(value)}
      </pre>
    </div>
  );
}
