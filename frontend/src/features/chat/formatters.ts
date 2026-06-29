import type { TeamActivityInfo, ToolCallInfo } from '../../hooks/useEventStream';
import type { StoredTeamActivity } from './types';
import { createId } from '../../utils/id';

export function normalizeStoredToolCalls(toolCalls: ToolCallInfo[] | undefined): ToolCallInfo[] {
  const seen = new Set<string>();
  const normalized: ToolCallInfo[] = [];

  for (const toolCall of toolCalls || []) {
    const id = String(toolCall.id || `${toolCall.name}-${normalized.length}`);
    const key = id || `${toolCall.name}-${toolCall.args}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const args = String(toolCall.args || '');
    const result = String(toolCall.result || '');
    normalized.push({
      id,
      name: normalizeStoredToolName(String(toolCall.name || 'tool'), args, result),
      args,
      result,
      status: (
        toolCall.status === 'calling' ||
        toolCall.status === 'running' ||
        toolCall.status === 'completed' ||
        toolCall.status === 'failed'
          ? toolCall.status
          : 'completed'
      ),
    });
  }

  return normalized;
}

function normalizeStoredToolName(name: string, args: string, result: string): string {
  const displayName = normalizeToolDisplayName(name);
  if (displayName !== 'tool') {
    return displayName;
  }
  const value = `${args}\n${result}`.toLowerCase();
  if (
    value.includes('补充信息') ||
    value.includes('用户拒绝') ||
    value.includes('human input') ||
    value.includes('askuser') ||
    value.includes('"questions"')
  ) {
    return 'AskUser';
  }
  return displayName;
}

export function normalizeToolDisplayName(name: string): string {
  const value = String(name || 'tool');
  const parts = value.split('_');
  if (parts.length === 2 && parts[0] && parts[0] === parts[1]) {
    return parts[0];
  }
  return value;
}

export function normalizeStoredTeamActivities(activities: StoredTeamActivity[]): TeamActivityInfo[] {
  return activities.map((activity) => ({
    id: String(activity.id || createId()),
    agentId: String(activity.agentId || activity.agent_id || ''),
    agentName: String(activity.agentName || activity.agent_name || 'Agent'),
    role: String(activity.role || 'worker'),
    status: (
      activity.status === 'pending' ||
      activity.status === 'running' ||
      activity.status === 'waiting' ||
      activity.status === 'completed' ||
      activity.status === 'failed' ||
      activity.status === 'skipped'
        ? activity.status
        : 'completed'
    ),
    task: String(activity.task || ''),
    detail: String(activity.detail || ''),
    output: String(activity.output || ''),
    thinking: String(activity.thinking || ''),
    currentEvent: String(activity.currentEvent || ''),
    toolCalls: normalizeStoredToolCalls(activity.toolCalls || activity.tool_calls || []),
    executionMode: String(activity.executionMode || 'parallel'),
    dependsOn: activity.dependsOn || [],
    waitReason: String(activity.waitReason || ''),
    dependencyOutputCount: Number(activity.dependencyOutputCount || 0),
    leaderContextCount: Number(activity.leaderContextCount || 0),
    createdAt: String(activity.createdAt || activity.created_at || ''),
  }));
}

/**
 * ChatPage 组件
 */
