/** Chat 右侧产物预览：路径、地址和类型判断。 */

export type FilePreviewKind = 'markdown' | 'text' | 'csv' | 'image' | 'pdf' | 'docx' | 'xlsx' | 'binary';

export type FilePreviewTarget = {
  sessionId: string;
  path: string;
  name: string;
};

export type FilePreviewPayload = {
  path: string;
  name: string;
  size: number;
  kind: FilePreviewKind;
  mediaType?: string;
  content: string;
  truncated: boolean;
};

export function downloadableArtifactPath(path: string, sessionId?: string): string {
  if (!sessionId) return '';
  const normalized = path.replaceAll('\\', '/');
  const marker = `.agent-workstation/sessions/${sessionId}/`;
  const markerIndex = normalized.indexOf(marker);
  if (markerIndex >= 0) return normalized.slice(markerIndex);
  return normalized.startsWith('/') ? '' : normalized;
}

export function artifactDownloadUrl(sessionId: string, path: string): string {
  const relative = downloadableArtifactPath(path, sessionId);
  if (!relative) return '';
  return `/api/tools/files/download?sessionId=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(relative)}`;
}

export function artifactPreviewUrl(sessionId: string, path: string): string {
  const relative = downloadableArtifactPath(path, sessionId);
  if (!relative) return '';
  return `/api/tools/files/preview?sessionId=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(relative)}`;
}

export function artifactContentUrl(sessionId: string, path: string): string {
  const relative = downloadableArtifactPath(path, sessionId);
  if (!relative) return '';
  return `/api/tools/files/content?sessionId=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(relative)}`;
}

export function isCanvasPreviewKind(kind?: FilePreviewKind): boolean {
  return kind === 'docx' || kind === 'pdf' || kind === 'image';
}

export function canUseSystemPreview(kind?: FilePreviewKind): boolean {
  return kind === 'docx' || kind === 'xlsx' || kind === 'pdf' || kind === 'image';
}

export function isDesktopFilePreviewEnabled(
  platform = typeof window !== 'undefined' ? window.electronAPI?.getPlatform() : undefined,
): boolean {
  return platform === 'darwin' || platform === 'win32';
}

export function hasElectronSystemPreview(): boolean {
  return isDesktopFilePreviewEnabled()
    && typeof window !== 'undefined'
    && typeof window.electronAPI?.embedFilePreview === 'function';
}

export function readPreviewHostRect(element: HTMLElement | null): {
  x: number;
  y: number;
  width: number;
  height: number;
} | null {
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  if (rect.width < 8 || rect.height < 8) return null;
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
}

export function fitDocxPreviewScale(containerWidth: number, pageWidth: number): number {
  if (containerWidth <= 0 || pageWidth <= 0) return 1;
  return Math.min(1, containerWidth / pageWidth);
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function parseCsvPreview(content: string): string[][] {
  return content
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0)
    .slice(0, 80)
    .map((line) => splitCsvLine(line).slice(0, 16));
}

function splitCsvLine(line: string): string[] {
  const cells: string[] = [];
  let current = '';
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
      continue;
    }
    if ((char === ',' || char === '\t') && !quoted) {
      cells.push(current);
      current = '';
      continue;
    }
    current += char;
  }
  cells.push(current);
  return cells;
}
