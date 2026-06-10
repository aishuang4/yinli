export function createId(prefix = ''): string {
  const randomUUID = globalThis.crypto?.randomUUID;
  const id = typeof randomUUID === 'function'
    ? randomUUID.call(globalThis.crypto)
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return prefix ? `${prefix}-${id}` : id;
}
