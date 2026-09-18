/**
 * ObservationPayload — 第二层按 kind 展示真实 input/output，方便对照送模内容。
 */

import { formatJsonPreview } from './traceUtils';

interface ObservationPayloadProps {
  kind: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export function ObservationPayload({ kind, input, output, metadata }: ObservationPayloadProps) {
  if (kind === 'generation') {
    return <GenerationPayload input={input} output={output} metadata={metadata} />;
  }
  if (kind === 'tool') {
    return <ToolPayload input={input} output={output} />;
  }
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <JsonPanel title="Input" value={input} />
      <JsonPanel title="Output" value={output} />
    </div>
  );
}

function GenerationPayload({
  input,
  output,
  metadata,
}: {
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}) {
  const messages = Array.isArray(input.messages) ? input.messages : [];
  const toolCalls = Array.isArray(output.toolCalls) ? output.toolCalls : [];
  const usage = isRecord(output.usage) ? output.usage : {};
  const system = textOf(input.system);
  const summary = textOf(input.summary);
  const thinking = textOf(output.thinking);
  const text = textOf(output.text);

  return (
    <div className="space-y-3">
      <section className="space-y-2">
        <SectionTitle>Input · 发给模型的内容</SectionTitle>
        {textOf(input.model) ? <MetaLine label="模型" value={textOf(input.model)} /> : null}
        {system ? <TextBlock title="System prompt" value={system} /> : null}
        {summary ? <TextBlock title="Context summary" value={summary} /> : null}
        {messages.length > 0 ? (
          <div className="space-y-2">
            <div className="text-xs text-[var(--color-text-tertiary)]">
              Messages · {messages.length} 条
              {input.truncatedMessages ? '（较早消息已截断）' : ''}
            </div>
            {messages.map((item, index) => (
              <MessageCard key={`msg-${index}`} message={item} />
            ))}
          </div>
        ) : (
          <EmptyHint text="还没有抓到 messages。新一轮对话后这里会列出实际上下文。" />
        )}
      </section>

      <section className="space-y-2">
        <SectionTitle>Output · 模型这一步回了什么</SectionTitle>
        {Object.keys(usage).length > 0 ? (
          <MetaLine
            label="Usage"
            value={[
              usage.inputTokens != null ? `in ${usage.inputTokens}` : '',
              usage.outputTokens != null ? `out ${usage.outputTokens}` : '',
            ].filter(Boolean).join(' · ') || formatJsonPreview(usage)}
          />
        ) : null}
        {textOf(output.finishedReason) ? <MetaLine label="结束原因" value={textOf(output.finishedReason)} /> : null}
        {thinking ? <TextBlock title="Thinking / reasoning" value={thinking} /> : <EmptyHint text="这一步没有 thinking" />}
        {text ? <TextBlock title="文本" value={text} /> : <EmptyHint text="这一步没有文本输出" />}
        {toolCalls.length > 0 ? (
          <div className="space-y-2">
            <div className="text-xs text-[var(--color-text-tertiary)]">接着调用的工具 · {toolCalls.length}</div>
            {toolCalls.map((item, index) => (
              <ToolCallCard key={`call-${index}`} value={item} />
            ))}
          </div>
        ) : (
          <EmptyHint text="这一步没有声明工具调用" />
        )}
      </section>

      {metadata && Object.keys(metadata).length > 0 ? <JsonPanel title="Metadata" value={metadata} /> : null}
    </div>
  );
}

function ToolPayload({
  input,
  output,
}: {
  input: Record<string, unknown>;
  output: Record<string, unknown>;
}) {
  const args = input.arguments !== undefined ? input.arguments : input;
  const result = toolResultText(output);
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <JsonPanel title="参数" value={args} />
      <div className="space-y-2">
        {result ? <TextBlock title="结果" value={result} /> : <EmptyHint text="没有捕获到工具输出" />}
        {textOf(output.error) ? <TextBlock title="错误" value={textOf(output.error)} /> : null}
        {textOf(output.state) ? <MetaLine label="状态" value={textOf(output.state)} /> : null}
      </div>
    </div>
  );
}

function MessageCard({ message }: { message: unknown }) {
  if (!isRecord(message)) {
    return <TextBlock title="message" value={String(message)} />;
  }
  const name = textOf(message.name);
  const text = textOf(message.text);
  const blocks = Array.isArray(message.blocks) ? message.blocks : [];
  const onlyHint = !text && blocks.length > 0 && blocks.every((item) => isRecord(item) && textOf(item.type) === 'hint');
  const role = onlyHint ? 'user' : (textOf(message.role) || 'message');
  return (
    <div className="rounded-md bg-[var(--color-bg-primary)] p-3">
      <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
        {role}{name && !onlyHint ? ` · ${name}` : ''}
      </div>
      {text ? <pre className={preClass}>{text}</pre> : null}
      {blocks.map((block, index) => (
        <MessageBlock key={`block-${index}`} block={block} />
      ))}
      {!text && blocks.length === 0 ? <EmptyHint text="这条消息没有正文" /> : null}
    </div>
  );
}

function MessageBlock({ block }: { block: unknown }) {
  if (!isRecord(block)) {
    return <pre className={preClass}>{String(block)}</pre>;
  }
  const type = textOf(block.type) || 'block';
  if (type === 'tool_call') {
    return <ToolCallCard value={block} />;
  }
  if (type === 'tool_result') {
    return (
      <div className="mt-2">
        <div className="text-[11px] text-[var(--color-text-tertiary)]">
          tool_result{textOf(block.name) ? ` · ${textOf(block.name)}` : ''}
        </div>
        <pre className={preClass}>{textOf(block.output) || formatJsonPreview(block)}</pre>
      </div>
    );
  }
  const body = textOf(block.text) || formatJsonPreview(block);
  if (type === 'text' || type === 'hint') {
    return <pre className={`${preClass} mt-2`}>{body}</pre>;
  }
  return (
    <div className="mt-2">
      <div className="text-[11px] text-[var(--color-text-tertiary)]">{type}</div>
      <pre className={preClass}>{body}</pre>
    </div>
  );
}

function ToolCallCard({ value }: { value: unknown }) {
  const record = isRecord(value) ? value : {};
  return (
    <div className="rounded-md bg-[var(--color-bg-primary)] p-3">
      <div className="text-xs font-medium text-[var(--color-text-primary)]">
        {textOf(record.name) || 'tool'}
        {textOf(record.id) ? ` · ${textOf(record.id)}` : ''}
      </div>
      <pre className={`${preClass} mt-1`}>{formatJsonPreview(record.input ?? record)}</pre>
    </div>
  );
}

function TextBlock({ title, value }: { title: string; value: string }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">{title}</div>
      <pre className={`${preClass} max-h-96`}>{value}</pre>
    </div>
  );
}

function JsonPanel({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">{title}</div>
      <pre className={`${preClass} max-h-96`}>{formatJsonPreview(value)}</pre>
    </div>
  );
}

function SectionTitle({ children }: { children: string }) {
  return <div className="text-xs font-medium text-[var(--color-text-secondary)]">{children}</div>;
}

function MetaLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-xs text-[var(--color-text-tertiary)]">
      {label} · {value}
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return <div className="text-xs text-[var(--color-text-tertiary)]">{text}</div>;
}

const preClass = 'overflow-auto whitespace-pre-wrap break-words rounded-md bg-[var(--color-bg-primary)] p-3 text-[11px] text-[var(--color-text-primary)]';

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function textOf(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function toolResultText(output: Record<string, unknown>): string {
  const direct = textOf(output.result) || textOf(output.output) || textOf(output.content);
  if (direct) return direct;
  if (Array.isArray(output.result)) {
    return output.result
      .map((item) => (isRecord(item) ? textOf(item.text) : typeof item === 'string' ? item : ''))
      .join('');
  }
  return '';
}
