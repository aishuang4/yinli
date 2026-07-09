import type { DisplayItem } from '../../types/displayEvents';
import type { SubagentTimelineItem } from '../../types/subagent';

export interface SubagentToolData {
  subagentName?: string;
  subagentInvocationId?: string;
  subagentStatus?: string;
  subagentTimeline?: SubagentTimelineItem[];
  subagentSummary?: string;
  subagentError?: string;
}

export function subagentToolDataFromDisplayItem(item: DisplayItem): SubagentToolData {
  return {
    subagentName: pickString(item.data.subagentName),
    subagentInvocationId: pickString(item.data.subagentInvocationId),
    subagentStatus: pickString(item.data.subagentStatus),
    subagentTimeline: normalizeSubagentTimeline(item.data.subagentTimeline),
    subagentSummary: pickString(item.data.subagentSummary),
    subagentError: pickString(item.data.subagentError),
  };
}

function normalizeSubagentTimeline(value: unknown): SubagentTimelineItem[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    .map((item, index) => ({
      id: pickString(item.id) || `subagent-step-${index}`,
      kind: pickString(item.kind) || 'event',
      type: pickString(item.type),
      title: pickString(item.title) || 'Subagent event',
      detail: pickString(item.detail),
      status: pickString(item.status) || 'running',
      timestamp: pickString(item.timestamp),
      toolName: pickString(item.toolName) || undefined,
    }));
  return items.length > 0 ? items : undefined;
}

function pickString(value: unknown): string {
  if (value === undefined || value === null) return '';
  return String(value);
}
