/** 在 Electron / Chromium 里按 Word 版式渲染 .docx。 */

import { useEffect, useRef, useState } from 'react';
import { resolveApiUrl } from '../../utils/apiUrl';
import { fitDocxPreviewScale } from './filePreview';

export function DocxPreviewView({
  contentUrl,
  name,
}: {
  contentUrl: string;
  name: string;
}) {
  const shellRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const styleRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const body = bodyRef.current;
    const style = styleRef.current;
    if (!body || !style || !contentUrl) return;
    const controller = new AbortController();
    body.replaceChildren();
    style.replaceChildren();
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const response = await fetch(resolveApiUrl(contentUrl), { signal: controller.signal });
        if (!response.ok) {
          throw new Error(await readDocxError(response));
        }
        const buffer = await response.arrayBuffer();
        const { renderAsync } = await import('docx-preview');
        if (controller.signal.aborted || bodyRef.current !== body) return;
        await renderAsync(buffer, body, style, {
          className: 'docx',
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: false,
          ignoreFonts: false,
          breakPages: true,
          ignoreLastRenderedPageBreak: false,
          experimental: true,
          renderHeaders: true,
          renderFooters: true,
          renderFootnotes: true,
          renderEndnotes: true,
        });
        if (controller.signal.aborted) return;
        setLoading(false);
      } catch (cause: unknown) {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : 'Word 预览失败');
        setLoading(false);
      }
    })();
    return () => {
      controller.abort();
      body.replaceChildren();
      style.replaceChildren();
    };
  }, [contentUrl]);

  useEffect(() => {
    const shell = shellRef.current;
    const body = bodyRef.current;
    if (!shell || !body || loading || error) return;
    const observer = new ResizeObserver(() => fitDocxToPane(shell, body));
    observer.observe(shell);
    return () => observer.disconnect();
  }, [error, loading]);

  return (
    <div ref={shellRef} className="chat-file-preview__docx-shell" aria-label={`Word 预览 ${name}`}>
      {loading ? <div className="chat-file-preview__status">正在渲染 Word 版式…</div> : null}
      {error ? <div className="chat-file-preview__status">{error}</div> : null}
      <div ref={styleRef} hidden />
      <div
        ref={bodyRef}
        className="chat-file-preview__docx"
        hidden={loading || Boolean(error)}
      />
    </div>
  );
}

function fitDocxToPane(shell: HTMLElement | null, body: HTMLElement | null): void {
  if (!shell || !body) return;
  const wrapper = body.querySelector('.docx-wrapper') as HTMLElement | null;
  if (!wrapper) return;
  wrapper.style.transform = 'none';
  body.style.width = '';
  body.style.height = '';
  const pageWidth = Math.max(
    wrapper.scrollWidth,
    ...Array.from(wrapper.querySelectorAll<HTMLElement>('section.docx')).map((page) => page.offsetWidth),
  );
  const scale = fitDocxPreviewScale(shell.clientWidth, pageWidth);
  wrapper.style.transformOrigin = 'top left';
  wrapper.style.transform = scale < 1 ? `scale(${scale})` : 'none';
  body.style.width = `${pageWidth * scale}px`;
  body.style.height = `${wrapper.scrollHeight * scale}px`;
}

async function readDocxError(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { detail?: unknown };
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
      return payload.detail;
    }
  } catch {
    /* 非 JSON */
  }
  return `Word 预览失败（HTTP ${response.status}）`;
}
