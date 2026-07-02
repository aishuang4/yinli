import type { HitlRequestInfo } from '../../hooks/useEventStream';

export function isAskUserToolName(name: string): boolean {
  const normalized = name.replace(/\s+/g, '').toLowerCase();
  return normalized === 'askuser' || normalized.endsWith('_askuser');
}

export function isAskUserHitlRequest(request: HitlRequestInfo): boolean {
  return request.toolCalls.some((toolCall) => isAskUserToolName(toolCall.rawName || toolCall.name));
}
