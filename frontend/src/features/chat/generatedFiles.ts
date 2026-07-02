import type { TeamActorInfo, ToolCallInfo } from '../../hooks/useEventStream';
import { normalizeToolDisplayName } from './formatters';

export type GeneratedFileInfo = {
  path: string;
  name: string;
  extension: string;
};

export function collectGeneratedFiles(toolCalls: ToolCallInfo[] = [], teamActors: TeamActorInfo[] = []): GeneratedFileInfo[] {
  const files: GeneratedFileInfo[] = [];
  const seen = new Set<string>();
  const addFile = (toolCall: ToolCallInfo) => {
    const file = getGeneratedFileInfo(toolCall);
    if (!file || seen.has(file.path)) return;
    seen.add(file.path);
    files.push(file);
  };

  toolCalls.forEach(addFile);
  teamActors.forEach((actor) => {
    (actor.toolCalls || []).forEach(addFile);
  });
  return files;
}

function getGeneratedFileInfo(toolCall: ToolCallInfo): GeneratedFileInfo | null {
  const toolName = normalizeToolDisplayName(toolCall.name).toLowerCase();
  if ((toolName !== 'write' && toolName !== 'edit') || toolCall.status !== 'completed') {
    return null;
  }
  const args = parseToolCallArgs(toolCall.args);
  const filePath = String(args.file_path || args.path || '').trim();
  if (!filePath) {
    return null;
  }
  const name = filePath.split(/[\\/]/).filter(Boolean).pop() || 'generated-file';
  const extension = name.includes('.') ? name.split('.').pop()?.toUpperCase() || 'FILE' : 'FILE';
  return { path: filePath, name, extension };
}

function parseToolCallArgs(raw: string): Record<string, unknown> {
  const value = raw.trim();
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}
