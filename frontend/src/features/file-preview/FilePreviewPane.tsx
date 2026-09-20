/** Chat 右侧产物预览：Markdown / 文本 / 表格；Electron 内嵌系统预览，否则栏内渲染。 */

import { useEffect, useRef, useState } from 'react';
import { Download, FileText, X } from 'lucide-react';
import { MarkdownContent } from '../../components/MarkdownContent';
import { resolveApiUrl } from '../../utils/apiUrl';
import { DocxPreviewView } from './DocxPreviewView';
import {
  artifactContentUrl,
  artifactDownloadUrl,
  artifactPreviewUrl,
  canUseSystemPreview,
  formatFileSize,
  hasElectronSystemPreview,
  isCanvasPreviewKind,
  isDesktopFilePreviewEnabled,
  parseCsvPreview,
  readPreviewHostRect,
  type FilePreviewKind,
  type FilePreviewPayload,
  type FilePreviewTarget,
} from './filePreview';
import './FilePreviewPane.css';

export function FilePreviewPane({
  target,
  onClose,
}: {
  target: FilePreviewTarget;
  onClose: () => void;
}) {
  const [payload, setPayload] = useState<FilePreviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [embedState, setEmbedState] = useState<'idle' | 'open' | 'fallback'>('idle');
  const hostRef = useRef<HTMLDivElement>(null);
  const previewUrl = artifactPreviewUrl(target.sessionId, target.path);
  const downloadUrl = artifactDownloadUrl(target.sessionId, target.path);
  const contentUrl = artifactContentUrl(target.sessionId, target.path);

  useEffect(() => {
    if (!isDesktopFilePreviewEnabled()) {
      setPayload(null);
      setError('当前环境不支持预览，请在 macOS 或 Windows 桌面端打开。');
      setLoading(false);
      setEmbedState('idle');
      return;
    }
    if (!previewUrl) {
      setPayload(null);
      setError('无法预览该路径');
      setLoading(false);
      setEmbedState('idle');
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setPayload(null);
    setEmbedState('idle');
    void window.electronAPI?.closeEmbeddedPreview?.();
    void fetch(resolveApiUrl(previewUrl), { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(await readPreviewError(response));
        }
        return response.json() as Promise<FilePreviewPayload>;
      })
      .then((data) => {
        if (controller.signal.aborted) return;
        setPayload(data);
        setLoading(false);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : '预览失败');
        setLoading(false);
      });
    return () => {
      controller.abort();
      void window.electronAPI?.closeEmbeddedPreview?.();
    };
  }, [previewUrl]);

  useEffect(() => {
    const host = hostRef.current;
    if (!payload || !hasElectronSystemPreview() || !canUseSystemPreview(payload.kind) || !payload.path) {
      setEmbedState(payload ? 'fallback' : 'idle');
      return;
    }
    let cancelled = false;
    const sync = async (mode: 'show' | 'update') => {
      const rect = readPreviewHostRect(hostRef.current);
      if (!rect || cancelled) return;
      if (mode === 'show') {
        const result = await window.electronAPI?.embedFilePreview?.({ path: payload.path, ...rect });
        if (!cancelled) setEmbedState(result?.ok ? 'open' : 'fallback');
        return;
      }
      if (!cancelled) {
        await window.electronAPI?.updateEmbeddedPreview?.(rect);
      }
    };
    void sync('show');
    const observer = new ResizeObserver(() => {
      void sync('update');
    });
    if (host) observer.observe(host);
    const onResize = () => {
      void sync('update');
    };
    window.addEventListener('resize', onResize);
    return () => {
      cancelled = true;
      observer.disconnect();
      window.removeEventListener('resize', onResize);
      void window.electronAPI?.closeEmbeddedPreview?.();
    };
  }, [payload]);

  const kind = payload?.kind;
  const title = payload?.name || target.name;
  const meta = [
    kindLabel(kind),
    payload ? formatFileSize(payload.size) : '',
    payload?.truncated ? '已截断' : '',
  ].filter(Boolean).join(' · ');

  return (
    <aside className="chat-file-preview" aria-label={`预览 ${title}`}>
      <header className="chat-file-preview__header">
        <div className="chat-file-preview__title">
          <div className="chat-file-preview__name" title={title}>{title}</div>
          <div className="chat-file-preview__meta">{meta || '正在打开'}</div>
        </div>
        {downloadUrl ? (
          <a
            href={resolveApiUrl(downloadUrl)}
            download={title}
            className="icon-button flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            aria-label={`下载 ${title}`}
          >
            <Download size={16} />
          </a>
        ) : null}
        <button
          type="button"
          className="icon-button flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
          onClick={onClose}
          aria-label="关闭预览"
        >
          <X size={16} />
        </button>
      </header>
      {isCanvasPreviewKind(kind) || embedState === 'open' ? null : (
        <div className="chat-file-preview__card">
          <div className="flex items-center gap-2">
            <FileText size={16} className="shrink-0 text-[var(--color-text-tertiary)]" />
            <div className="min-w-0">
              <div className="chat-file-preview__card-name">{title}</div>
              <div className="chat-file-preview__card-meta">{meta}</div>
            </div>
          </div>
        </div>
      )}
      <div
        ref={hostRef}
        className={`chat-file-preview__body${isCanvasPreviewKind(kind) || embedState === 'open' ? ' chat-file-preview__body--canvas' : ''}${embedState === 'open' ? ' chat-file-preview__body--embedded' : ''}`}
      >
        {loading || (embedState === 'idle' && payload && canUseSystemPreview(kind)) ? (
          <div className="chat-file-preview__status">正在加载预览…</div>
        ) : null}
        {!loading && error ? <div className="chat-file-preview__status">{error}</div> : null}
        {!loading && !error && payload && embedState === 'fallback' ? (
          <PreviewBody payload={payload} contentUrl={contentUrl} downloadUrl={downloadUrl} />
        ) : null}
      </div>
    </aside>
  );
}

function PreviewBody({
  payload,
  contentUrl,
  downloadUrl,
}: {
  payload: FilePreviewPayload;
  contentUrl: string;
  downloadUrl: string;
}) {
  if (payload.kind === 'image' && contentUrl) {
    return (
      <img
        className="chat-file-preview__image"
        src={resolveApiUrl(contentUrl)}
        alt={payload.name}
      />
    );
  }
  if (payload.kind === 'pdf' && contentUrl) {
    return (
      <iframe
        className="chat-file-preview__pdf"
        title={payload.name}
        src={resolveApiUrl(contentUrl)}
      />
    );
  }
  if (payload.kind === 'docx' && contentUrl) {
    return <DocxPreviewView contentUrl={contentUrl} name={payload.name} />;
  }
  if (payload.kind === 'markdown') {
    return (
      <>
        <MarkdownContent content={payload.content || '（空文档）'} />
        {payload.truncated ? <p className="chat-file-preview__hint">内容过长，仅展示前 200 KB。</p> : null}
      </>
    );
  }
  if (payload.kind === 'csv' || payload.kind === 'xlsx') {
    const rows = parseCsvPreview(payload.content);
    if (rows.length === 0) {
      return <div className="chat-file-preview__status">（空表格）</div>;
    }
    const [header, ...body] = rows;
    return (
      <>
        <div className="chat-file-preview__table-wrap">
          <table className="chat-file-preview__table">
            <thead>
              <tr>
                {header.map((cell, index) => <th key={`${index}-${cell}`}>{cell || ' '}</th>)}
              </tr>
            </thead>
            <tbody>
              {body.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {header.map((_, index) => <td key={`${rowIndex}-${index}`}>{row[index] || ''}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {payload.truncated ? <p className="chat-file-preview__hint">仅展示前若干行，完整内容请下载。</p> : null}
      </>
    );
  }
  if (payload.kind === 'text') {
    return (
      <>
        <pre className="chat-file-preview__text">{payload.content}</pre>
        {payload.truncated ? <p className="chat-file-preview__hint">内容过长，仅展示前 200 KB。</p> : null}
      </>
    );
  }
  return (
    <div className="chat-file-preview__status">
      <div>此格式暂不支持在线预览。</div>
      {downloadUrl ? (
        <a
          href={resolveApiUrl(downloadUrl)}
          download={payload.name}
          className="mt-3 inline-flex text-sm text-[var(--color-text-primary)] underline"
        >
          下载原文件
        </a>
      ) : null}
    </div>
  );
}

function kindLabel(kind?: FilePreviewKind): string {
  if (kind === 'markdown') return 'Markdown';
  if (kind === 'csv') return 'CSV';
  if (kind === 'text') return '文本';
  if (kind === 'image') return '图片';
  if (kind === 'pdf') return 'PDF';
  if (kind === 'docx') return 'Word';
  if (kind === 'xlsx') return 'Excel';
  if (kind === 'binary') return '文件';
  return '';
}

async function readPreviewError(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { detail?: unknown };
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
      return payload.detail;
    }
  } catch {
    /* 非 JSON */
  }
  return `预览失败（HTTP ${response.status}）`;
}
