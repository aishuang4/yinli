/**
 * traceUtils — 运行轨迹展示辅助函数。
 */

import type { TraceEvent, TraceEventFilter } from './traceTypes';

export const TRACE_FILTERS: Array<{ key: TraceEventFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'runtime', label: 'Runtime' },
  { key: 'permissions', label: '权限审计' },
  { key: 'tools', label: '工具' },
  { key: 'models', label: '模型' },
  { key: 'hitl', label: '人工介入' },
];

export const TRACE_RENDER_BATCH_SIZE = 30;
export const MAX_MESSAGE_PREVIEW_LENGTH = 2500;
export const MAX_PAYLOAD_PREVIEW_LENGTH = 3000;

export function shouldShowTracePayload(event: TraceEvent) {
  return (
    event.eventType === 'WORKSPACE_CONFIG' ||
    event.eventType === 'PERMISSION_POLICY' ||
    event.eventType === 'PERMISSION_DECISION' ||
    event.eventType === 'MODEL_CACHE_USAGE' ||
    event.eventType === 'COMMAND_RESULT' ||
    event.eventType === 'FILE_CHANGE' ||
    event.eventType === 'RUNTIME_EVENT' ||
    event.eventType.startsWith('MODEL_FALLBACK_')
  );
}

export function matchesTraceFilter(event: TraceEvent, filter: TraceEventFilter) {
  if (filter === 'all') return true;
  if (filter === 'runtime') return isRuntimeEvent(event);
  if (filter === 'permissions') return isPermissionEvent(event);
  if (filter === 'tools') return event.eventType.includes('TOOL') || event.eventType === 'COMMAND_RESULT' || event.eventType === 'FILE_CHANGE';
  if (filter === 'models') return event.eventType.startsWith('MODEL_');
  if (filter === 'hitl') return event.eventType.includes('HITL') || event.eventType.includes('CLARIFICATION');
  return true;
}

export function isRuntimeEvent(event: TraceEvent) {
  return event.eventType === 'RUNTIME_EVENT';
}

export function isPermissionEvent(event: TraceEvent) {
  return event.eventType === 'PERMISSION_POLICY' || event.eventType === 'PERMISSION_DECISION';
}

export function permissionDecisionColor(decision: string) {
  if (decision === 'deny') return 'red';
  if (decision === 'ask') return 'orange';
  if (decision === 'allow' || decision === 'configured') return 'green';
  return 'transparent';
}

export function getRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function formatPayloadPreview(payload: Record<string, unknown>): string {
  const text = JSON.stringify(payload || {}, null, 2);
  return formatLongTextPreview(text, MAX_PAYLOAD_PREVIEW_LENGTH);
}

export function formatLongTextPreview(text: string, limit: number): string {
  const value = String(text || '');
  if (value.length <= limit) return value;
  return `${value.slice(0, limit)}\n\n...（内容过长，已在运行轨迹页截断）`;
}

export function parseHttpToolSummary(text: string): { method: string; url: string; statusCode: number; durationMs: number } | null {
  if (!text) return null;
  try {
    const data = JSON.parse(text);
    const method = String(data.method || '');
    const url = String(data.url || '');
    const statusCode = Number(data.status_code || 0);
    const durationMs = Number(data.duration_ms || 0);
    if (!method || !url || !statusCode) return null;
    return { method, url, statusCode, durationMs };
  } catch {
    return null;
  }
}

export async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
}

export function normalizeToolDisplayName(name: string): string {
  const value = String(name || 'tool');
  const parts = value.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return value;
}

export function formatTraceTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
