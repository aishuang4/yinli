import { useCallback, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Copy, Download, FileText, Loader2, Wrench, XCircle } from 'lucide-react';
import type { ToolCallInfo } from '../../hooks/useEventStream';
import { copyTextToClipboard } from './chatUiUtils';
import type { GeneratedFileInfo } from './generatedFiles';

/**
 * ToolCallBlock — 工具调用展示组件
 *
 * 显示工具名称、参数和执行结果。
 * 可展开/折叠查看详情。
 */
export function GeneratedFilesBlock({ files }: { files: GeneratedFileInfo[] }) {
  if (files.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] shadow-sm">
      {files.map((file, index) => (
        <div
          key={file.path}
          className={`flex min-w-0 items-center gap-3 px-4 py-3 ${index > 0 ? 'border-t border-[var(--color-border)]' : ''}`}
        >
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-primary)] text-[var(--color-text-tertiary)]">
            <FileText size={21} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]" title={file.name}>
              {file.name}
            </div>
            <div className="mt-0.5 truncate text-xs text-[var(--color-text-tertiary)]" title={file.path}>
              文档 · {file.extension}
            </div>
          </div>
          <a
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-sm font-medium text-[var(--color-text-primary)] transition hover:bg-[var(--color-bg-hover)]"
            href={`/api/tools/files/download?path=${encodeURIComponent(file.path)}`}
            download={file.name}
            title={file.path}
          >
            <Download size={15} />
            下载
          </a>
        </div>
      ))}
    </div>
  );
}

export function ToolCallBlock({ toolCall }: { toolCall: ToolCallInfo }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState<'args' | 'result' | null>(null);
  const isRunning = toolCall.status === 'calling' || toolCall.status === 'running';
  const isFailed = toolCall.status === 'failed';
  const displayArgs = toolCall.args;
  const displayResult = toolCall.result;

  const handleCopyToolText = useCallback(async (kind: 'args' | 'result', value: string) => {
    if (!value.trim()) return;
    await copyTextToClipboard(value);
    setCopied(kind);
    window.setTimeout(() => setCopied(null), 1200);
  }, []);

  return (
    <div
      style={{
        borderRadius: 'var(--radius-lg)',
        border: `1px solid ${isFailed ? 'var(--color-error)' : isRunning ? 'var(--color-accent)' : 'var(--color-border)'}`,
        overflow: 'hidden',
        fontSize: 'var(--text-sm)',
      }}
    >
      {/* 头部 */}
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          width: '100%',
          padding: '6px 10px',
          border: 'none',
          backgroundColor: 'transparent',
          color: 'var(--color-text-primary)',
          cursor: 'pointer',
          textAlign: 'start',
        }}
      >
        {isRunning ? (
          <Loader2 size={12} className="shrink-0 animate-spin" style={{ color: 'var(--color-accent)' }} />
        ) : isFailed ? (
          <XCircle size={12} className="shrink-0" style={{ color: 'var(--color-error)' }} />
        ) : (
          <Wrench size={12} className="shrink-0" style={{ color: 'var(--color-success)' }} />
        )}
        <span
          title={toolCall.name}
          style={{
            minWidth: 0,
            flex: 1,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontWeight: 500,
          }}
        >
          {toolCall.name}
        </span>
        <span
          style={{
            fontSize: 'var(--text-xs)',
            color: isFailed ? 'var(--color-error)' : isRunning ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
            flexShrink: 0,
          }}
        >
          {isFailed ? '失败' : isRunning ? '执行中...' : '完成'}
        </span>
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>

      {/* 展开详情 */}
      {expanded && (
        <div
          style={{
            borderTop: '1px solid var(--color-border)',
            padding: '8px 10px',
            backgroundColor: 'var(--color-bg-secondary)',
          }}
        >
          {displayArgs && (
            <div style={{ marginBottom: '6px' }}>
              <div
                style={{
                  fontSize: 'var(--text-xs)',
                  color: 'var(--color-text-tertiary)',
                  marginBottom: '2px',
                }}
              >
                <div className="flex items-center justify-between gap-2">
                  <span>参数</span>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
                    onClick={() => void handleCopyToolText('args', displayArgs)}
                  >
                    {copied === 'args' ? <Check size={12} /> : <Copy size={12} />}
                    {copied === 'args' ? '已复制' : '复制'}
                  </button>
                </div>
              </div>
              <pre
                style={{
                  fontSize: 'var(--text-xs)',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--color-text-secondary)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  margin: 0,
                  maxHeight: '120px',
                  overflow: 'auto',
                }}
              >
                {displayArgs}
              </pre>
            </div>
          )}
          {displayResult && (
            <div>
              <div
                style={{
                  fontSize: 'var(--text-xs)',
                  color: 'var(--color-text-tertiary)',
                  marginBottom: '2px',
                }}
              >
                <div className="flex items-center justify-between gap-2">
                  <span>结果</span>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
                    onClick={() => void handleCopyToolText('result', displayResult)}
                  >
                    {copied === 'result' ? <Check size={12} /> : <Copy size={12} />}
                    {copied === 'result' ? '已复制' : '复制'}
                  </button>
                </div>
              </div>
              <pre
                style={{
                  fontSize: 'var(--text-xs)',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--color-text-secondary)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  margin: 0,
                  maxHeight: '150px',
                  overflow: 'auto',
                }}
              >
                {displayResult}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
