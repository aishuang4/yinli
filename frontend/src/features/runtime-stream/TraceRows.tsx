/** 终态过程：回答出现后收成一行；横线上面是这则消息的状态。 */

import { useState } from 'react';
import { Brain, ChevronRight, Globe, Loader2 } from 'lucide-react';
import { toolIcon } from './toolIcon';
import { ToolInputPreview } from './ToolInputPreview';
import {
  bashToolView,
  blockToolFamily,
  toolCallName,
  toolDetail,
  webSearchDetail,
  type WebPageResult,
} from './processTrace';
import type { DisplayBlock } from './protocol';

/** 横线上面是这则消息的状态。有过程时可展开，正文始终留在线下面。 */
export function MessageStatus({
  label,
  expandable = false,
  children,
}: {
  label: string;
  expandable?: boolean;
  children?: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div>
      <div className="pb-2">
        {expandable ? (
          <TraceToggle
            label={label}
            expanded={expanded}
            onToggle={() => setExpanded((current) => !current)}
          />
        ) : (
          <div className="px-1 text-[13px] leading-5 text-[var(--color-text-tertiary)]">{label}</div>
        )}
      </div>
      <div className="border-t border-[var(--color-border)]" />
      {expandable ? (
        <div hidden={!expanded} className={expanded ? 'mt-3 space-y-3' : undefined}>{children}</div>
      ) : null}
    </div>
  );
}

export function CollapsibleTrace({
  label,
  icon,
  spinning = false,
  children,
}: {
  label: string;
  icon?: React.ReactNode;
  spinning?: boolean;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div>
      <TraceToggle
        label={label}
        icon={icon}
        spinning={spinning}
        expanded={expanded}
        onToggle={() => setExpanded((current) => !current)}
      />
      <div hidden={!expanded} className={expanded ? 'mt-1.5 space-y-1 pl-0.5' : undefined}>{children}</div>
    </div>
  );
}

/** 一次工具调用：图标和名称一直可见，参数和结果默认收着。 */
export function ToolCallRow({ tool }: { tool: DisplayBlock }) {
  const name = toolCallName(tool);
  const bash = bashToolView(tool);
  const label = bash?.summary ? `${name} · ${bash.summary}` : name;
  const summary = bash ? <BashDetails command={bash.command} note={bash.note} /> : toolCallDetails(tool);
  const input = (tool.data as Record<string, unknown>)?.inputPreview;
  const details = summary || input ? <>{summary}<ToolInputPreview value={input} /></> : null;
  const icon = <span className="flex h-4 w-4 items-center justify-center">{toolIcon(name)}</span>;
  if (!details) {
    return (
      <div className="min-w-0">
        <LiveTraceLabel label={label} icon={icon} spinning={tool.phase === 'open'} />
      </div>
    );
  }
  return (
    <CollapsibleTrace label={label} icon={icon} spinning={tool.phase === 'open'}>
      {details}
    </CollapsibleTrace>
  );
}

function BashDetails({ command, note }: { command: string; note: string }) {
  return (
    <div className="ml-[7px] border-l border-[var(--color-border)] py-1 pl-3">
      <pre className="m-0 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[var(--color-bg-tertiary)] px-2.5 py-2 font-mono text-[11px] leading-5 text-[var(--color-text-secondary)]">
        {command}
      </pre>
      {note ? <div className="mt-1 break-words text-xs leading-5 text-[var(--color-text-tertiary)]">{note}</div> : null}
    </div>
  );
}

function toolCallDetails(tool: DisplayBlock): React.ReactNode {
  if (blockToolFamily(tool) === 'web') {
    const detail = webSearchDetail([tool]);
    if (!detail.pages.length) return null;
    return <WebSearchList query={detail.query} pages={detail.pages} />;
  }
  const detail = toolDetail(tool);
  const title = detail.title === toolCallName(tool) ? '' : detail.title;
  if (!title && !detail.note) return null;
  return (
    <div className="ml-[7px] min-w-0 border-l border-[var(--color-border)] py-1 pl-3 text-xs leading-5 text-[var(--color-text-tertiary)]">
      {title ? <div className="break-words">{title}</div> : null}
      {detail.note ? <div className="mt-1 break-words">{detail.note}</div> : null}
    </div>
  );
}

export function ThinkingTrace({ children }: { children: React.ReactNode }) {
  return (
    <CollapsibleTrace label="思考" icon={<Brain size={14} strokeWidth={1.75} />}>
      {children}
    </CollapsibleTrace>
  );
}

/** 运行中的状态行：只有标签，不能收起。 */
export function LiveTraceLabel({
  label,
  icon,
  spinning = false,
}: {
  label: string;
  icon?: React.ReactNode;
  spinning?: boolean;
}) {
  return (
    <div className="flex w-full min-w-0 items-center gap-1.5 px-1 py-0.5 text-[13px] text-[var(--color-text-tertiary)]">
      {icon ? <span className="flex h-4 w-4 shrink-0 items-center justify-center">{icon}</span> : null}
      {spinning ? <Loader2 size={14} className="shrink-0 animate-spin" /> : null}
      <span className="min-w-0 truncate">{label}</span>
    </div>
  );
}

/** 思考默认收起。展开后限高滚动，避免整段推理把对话撑满。 */
export function LiveThinking({
  spinning = false,
  children,
}: {
  spinning?: boolean;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div>
      <TraceToggle
        label="思考"
        icon={<Brain size={14} strokeWidth={1.75} />}
        spinning={spinning}
        expanded={expanded}
        onToggle={() => setExpanded((current) => !current)}
      />
      <div hidden={!expanded}>
        <div className="thinking-trace ml-[7px] mt-1 max-h-40 overflow-auto border-l border-[var(--color-border)] py-1 pl-3">
          {children}
        </div>
      </div>
    </div>
  );
}

export function LiveActivity({
  label,
  spinning = false,
  children,
}: {
  label: string;
  spinning?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div>
      <LiveTraceLabel label={label} spinning={spinning} />
      {children ? <div className="mt-1.5">{children}</div> : null}
    </div>
  );
}

export function WebSearchList({
  query,
  pages,
}: {
  query: string;
  pages: WebPageResult[];
}) {
  if (!pages.length) return null;
  return (
    <div className="mt-1 text-[13px] leading-5 text-[var(--color-text-tertiary)]">
      <div className="flex min-w-0 items-center gap-2 px-1">
        <Globe size={14} strokeWidth={1.75} className="shrink-0" />
        <span className="shrink-0">已搜索网页</span>
        {query ? <span className="min-w-0 truncate">{query}</span> : null}
      </div>
      <div className="ml-[7px] mt-1 border-l border-[var(--color-border)] py-1 pl-3">
        <ol className="m-0 list-none space-y-1.5 p-0">
          {pages.map((page, index) => (
            <li key={`${page.host}:${page.url}:${index}`} className="flex min-w-0 items-center gap-2">
              <span className="w-4 shrink-0 text-right tabular-nums">{index + 1}</span>
              <SiteMark host={page.host} />
              <PageTitle page={page} />
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function SiteMark({ host }: { host: string }) {
  const [failed, setFailed] = useState(false);
  if (!host || failed) {
    return <Globe size={14} strokeWidth={1.75} className="shrink-0" />;
  }
  return (
    <img
      src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=64`}
      alt=""
      width={16}
      height={16}
      referrerPolicy="no-referrer"
      className="h-4 w-4 shrink-0 rounded-[3px] object-contain"
      onError={() => setFailed(true)}
    />
  );
}

function PageTitle({ page }: { page: WebPageResult }) {
  const className = 'min-w-0 flex-1 truncate text-[var(--color-text-tertiary)] no-underline';
  if (!page.url) return <span className={className}>{page.title}</span>;
  return (
    <a href={page.url} target="_blank" rel="noreferrer" className={`${className} hover:underline`}>
      {page.title}
    </a>
  );
}

function TraceToggle({
  label,
  icon,
  spinning = false,
  expanded,
  onToggle,
}: {
  label: string;
  icon?: React.ReactNode;
  spinning?: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      aria-expanded={expanded}
      className="flex w-full min-w-0 cursor-pointer items-center gap-1.5 border-0 bg-transparent px-1 py-0.5 text-left text-[13px] text-[var(--color-text-tertiary)]"
      onClick={onToggle}
    >
      {icon ? <span className="flex h-4 w-4 shrink-0 items-center justify-center">{icon}</span> : null}
      <span className="min-w-0 truncate">{label}</span>
      {spinning ? <Loader2 size={14} className="shrink-0 animate-spin" /> : null}
      <ChevronRight
        size={14}
        className={`shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`}
      />
    </button>
  );
}
