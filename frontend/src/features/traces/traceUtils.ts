/**
 * traceUtils — 运行轨迹时间线展示辅助。
 */

import type { ObservationKind, TraceObservation } from './traceTypes';

export const MAX_PAYLOAD_PREVIEW_LENGTH = 16000;

const KIND_LABEL: Record<string, string> = {
  run: '整轮',
  generation: '模型',
  tool: '工具',
  skill: 'Skill',
  approval: '等待确认',
  subagent: 'Subagent',
  'team-task': 'Team 任务',
};

export function observationKindLabel(kind: ObservationKind | string): string {
  return KIND_LABEL[kind] || kind;
}

export function formatTraceTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return '进行中';
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const remain = Math.round(seconds % 60);
  if (minutes < 60) return remain ? `${minutes}m ${remain}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainMinutes = minutes % 60;
  return remainMinutes ? `${hours}h ${remainMinutes}m` : `${hours}h`;
}

export function formatJsonPreview(value: unknown): string {
  const text = JSON.stringify(value ?? {}, null, 2);
  if (text.length <= MAX_PAYLOAD_PREVIEW_LENGTH) return text;
  return `${text.slice(0, MAX_PAYLOAD_PREVIEW_LENGTH)}\n\n...（已截断，完整内容以 Chat 历史为准）`;
}

export function observationAccent(kind: string, status: string): string {
  if (status === 'failed') return 'var(--color-error)';
  if (kind === 'approval' || status.startsWith('waiting')) return 'var(--color-warning, #d97706)';
  if (kind === 'generation') return 'var(--color-accent)';
  if (kind === 'tool') return 'var(--color-success, #16a34a)';
  if (kind === 'skill') return '#7c3aed';
  if (kind === 'subagent') return '#0f766e';
  if (kind === 'team-task') return '#0369a1';
  return 'var(--color-text-tertiary)';
}

export function timelineRange(observations: TraceObservation[], nowMs = Date.now()) {
  const starts = observations
    .map((item) => Date.parse(item.startedAt))
    .filter((value) => Number.isFinite(value));
  const start = starts.length ? Math.min(...starts) : nowMs;
  const ends = observations.map((item) => (
    item.endedAt ? Date.parse(item.endedAt) : nowMs
  )).filter((value) => Number.isFinite(value));
  const end = Math.max(start + 1, ends.length ? Math.max(...ends) : nowMs);
  return { start, end, durationMs: end - start };
}

export interface TimelineLane {
  observation: TraceObservation;
  lane: number;
  left: number;
  width: number;
}

export function layoutTimelineLanes(
  observations: TraceObservation[],
  nowMs = Date.now(),
): { lanes: TimelineLane[]; laneCount: number; range: ReturnType<typeof timelineRange> } {
  const range = timelineRange(observations, nowMs);
  const span = Math.max(1, range.end - range.start);
  const segments = observations
    .filter((item) => item.kind !== 'run')
    .slice()
    .sort((left, right) => Date.parse(left.startedAt) - Date.parse(right.startedAt));
  const lanes: TimelineLane[] = segments.map((observation, lane) => {
    const started = Date.parse(observation.startedAt);
    const ended = observation.endedAt ? Date.parse(observation.endedAt) : nowMs;
    const safeStart = Number.isFinite(started) ? started : range.start;
    const safeEnd = Number.isFinite(ended) ? Math.max(safeStart + 1, ended) : range.end;
    const left = ((safeStart - range.start) / span) * 100;
    const width = Math.max(1.6, ((safeEnd - safeStart) / span) * 100);
    return { observation, lane, left, width: Math.min(width, 100 - left) };
  });
  return { lanes, laneCount: Math.max(1, lanes.length), range };
}
