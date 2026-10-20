/** Chat 工具入参：与 Trace 共用后端脱敏结果，不用工具摘要替代完整参数。 */

export function ToolInputPreview({ value }: { value: unknown }) {
  if (value == null || value === '') return null;
  let content = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  if (typeof value === 'string') {
    try { content = JSON.stringify(JSON.parse(value), null, 2); } catch { /* 未完整的旧预览按文本显示。 */ }
  }
  return (
    <div className="mt-1 min-w-0">
      <div className="mb-1 text-[11px] text-[var(--color-text-tertiary)]">入参</div>
      <pre className="m-0 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[var(--color-bg-tertiary)] px-2.5 py-2 font-mono text-[11px] leading-5 text-[var(--color-text-secondary)]">
        {content}
      </pre>
    </div>
  );
}
