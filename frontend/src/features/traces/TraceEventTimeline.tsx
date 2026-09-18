/**
 * TraceEventTimeline — 一行一步的墙钟时间线：左侧名称/耗时，右侧对齐总时长。
 */

import type { ReactNode } from 'react';
import type { TraceDetail, TraceObservation } from './traceTypes';
import {
  formatDuration,
  layoutTimelineLanes,
  observationAccent,
  observationKindLabel,
} from './traceUtils';

interface TraceEventTimelineProps {
  trace: TraceDetail;
  selectedId: string;
  onSelect: (observation: TraceObservation) => void;
}

export function TraceEventTimeline({
  trace,
  selectedId,
  onSelect,
}: TraceEventTimelineProps) {
  const { lanes, range } = layoutTimelineLanes(trace.observations);
  const root = trace.observations.find((item) => item.kind === 'run') || null;

  return (
    <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-sm font-medium text-[var(--color-text-primary)]">墙钟时间线</div>
        <div className="text-xs text-[var(--color-text-tertiary)]">
          总耗时 {formatDuration(trace.durationMs ?? range.durationMs)}
        </div>
      </div>

      <div className="mb-2 grid min-w-0 grid-cols-[minmax(0,220px)_64px_minmax(0,1fr)] items-center gap-2 px-1 text-[11px] text-[var(--color-text-tertiary)]">
        <span>步骤</span>
        <span className="text-right">耗时</span>
        <span className="flex justify-between px-1">
          <span>0</span>
          <span>{formatDuration(trace.durationMs ?? range.durationMs)}</span>
        </span>
      </div>

      <button
        type="button"
        onClick={() => root && onSelect(root)}
        className={rowClass(selectedId === root?.id)}
      >
        <RowLabel kind="run" name={root?.name || '整轮'} />
        <span className="text-right text-[11px] text-[var(--color-text-tertiary)]">
          {formatDuration(root?.durationMs ?? trace.durationMs)}
        </span>
        <Track>
          <Bar
            left={0}
            width={100}
            color={observationAccent('run', root?.status || trace.status)}
            selected={selectedId === root?.id}
          />
        </Track>
      </button>

      {lanes.length === 0 ? (
        <div className="mt-2 rounded-md bg-[var(--color-bg-primary)] p-6 text-center text-sm text-[var(--color-text-tertiary)]">
          本轮还没有可展示的步骤
        </div>
      ) : (
        <div className="mt-1 max-h-80 min-w-0 space-y-1 overflow-x-hidden overflow-y-auto">
          {lanes.map((item) => {
            const selected = selectedId === item.observation.id;
            const waiting = item.observation.kind === 'approval' || item.observation.status.startsWith('waiting');
            return (
              <button
                key={item.observation.id}
                type="button"
                title={`${observationKindLabel(item.observation.kind)} · ${item.observation.name}`}
                onClick={() => onSelect(item.observation)}
                className={rowClass(selected)}
              >
                <RowLabel kind={item.observation.kind} name={item.observation.name} />
                <span className="text-right text-[11px] text-[var(--color-text-tertiary)]">
                  {formatDuration(item.observation.durationMs)}
                </span>
                <Track>
                  <Bar
                    left={item.left}
                    width={item.width}
                    color={observationAccent(item.observation.kind, item.observation.status)}
                    selected={selected}
                    dashed={waiting}
                  />
                </Track>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function RowLabel({ kind, name }: { kind: string; name: string }) {
  return (
    <span className="flex min-w-0 items-center gap-2 text-left">
      <span
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ background: observationAccent(kind, 'completed') }}
      />
      <span className="truncate text-xs text-[var(--color-text-primary)]">
        {observationKindLabel(kind)} · {name}
      </span>
    </span>
  );
}

function Track({ children }: { children: ReactNode }) {
  return (
    <span className="relative block h-6 min-w-0 overflow-hidden rounded-sm bg-[var(--color-bg-primary)]">
      <span className="pointer-events-none absolute inset-x-1 top-1/2 h-px -translate-y-1/2 bg-[var(--color-border)]" />
      <span className="absolute inset-y-[3px] inset-x-1 min-w-0">{children}</span>
    </span>
  );
}

function Bar({
  left,
  width,
  color,
  selected,
  dashed = false,
}: {
  left: number;
  width: number;
  color: string;
  selected: boolean;
  dashed?: boolean;
}) {
  return (
    <span
      className={[
        'absolute inset-y-0 rounded-sm',
        selected ? 'ring-2 ring-inset ring-[var(--color-accent)]' : '',
        dashed ? 'border border-dashed border-white/70' : '',
      ].join(' ')}
      style={{
        left: `${left}%`,
        width: `${width}%`,
        background: color,
      }}
    />
  );
}

function rowClass(selected: boolean) {
  return [
    'grid w-full min-w-0 grid-cols-[minmax(0,220px)_64px_minmax(0,1fr)] items-center gap-2 rounded-md px-1 py-0.5 text-left',
    selected ? 'bg-[var(--color-accent-bg)]' : 'hover:bg-[var(--color-bg-hover)]',
  ].join(' ');
}
