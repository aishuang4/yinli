/** 刷新可恢復的 Session 導航。只改 query，不改路徑。 */

export interface SessionLocation {
  sessionId: string;
  view: string;
  jobId: string;
  traceId: string;
  runId: string;
  studioId: string;
  previewPath: string;
}

const EMPTY: SessionLocation = {
  sessionId: '',
  view: '',
  jobId: '',
  traceId: '',
  runId: '',
  studioId: '',
  previewPath: '',
};

export function readSessionLocation(search: string): SessionLocation {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  return {
    sessionId: params.get('session') || '',
    view: params.get('view') || '',
    jobId: params.get('job') || '',
    traceId: params.get('trace') || '',
    runId: params.get('run') || '',
    studioId: params.get('studio') || '',
    previewPath: params.get('preview') || '',
  };
}

export function writeSessionLocation(current: string, patch: Partial<SessionLocation>): string {
  const params = new URLSearchParams(current.startsWith('?') ? current.slice(1) : current);
  const next = { ...readSessionLocation(current), ...patch };
  const pairs: Array<[keyof SessionLocation, string]> = [
    ['sessionId', 'session'],
    ['view', 'view'],
    ['jobId', 'job'],
    ['traceId', 'trace'],
    ['runId', 'run'],
    ['studioId', 'studio'],
    ['previewPath', 'preview'],
  ];
  for (const [field, key] of pairs) {
    const value = next[field];
    if (value) params.set(key, value);
    else params.delete(key);
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

export function emptySessionLocation(): SessionLocation {
  return { ...EMPTY };
}
