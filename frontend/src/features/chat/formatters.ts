import type { ToolCallInfo } from '../../hooks/useEventStream';

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
