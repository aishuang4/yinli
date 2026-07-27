/** Chat Runtime Block 的领域 renderer。 */

import { useContext, useState } from 'react';
import {
  AlertCircle,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Circle,
  CircleDot,
  ChevronRight,
  Download,
  FilePenLine,
  FilePlus2,
  FileSearch,
  FileText,
  FolderSearch,
  Globe2,
  ListChecks,
  Loader2,
  MessageCircleQuestion,
  Puzzle,
  Terminal,
  Users,
  Wrench,
} from 'lucide-react';
import { MarkdownContent } from '../../components/MarkdownContent';
import { HitlApprovalBlock } from '../chat/HumanInteractionBlocks';
import {
  buildClarificationRequestFromAskUser,
  isAskUserToolName,
  toHitlRequest,
} from '../chat/humanInteractionUtils';
import { resolveApiUrl } from '../../utils/apiUrl';
import type { DisplayBlock } from './protocol';
import { RuntimeBlockActionsContext } from './runtimeBlockActions';

export type RecordData = Record<string, unknown>;

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function statusLabel(block: DisplayBlock): string {
  if (block.phase === 'open') return '进行中';
  if (block.outcome === 'failed') return '失败';
  if (block.outcome === 'cancelled') return '已取消';
  return '';
}

function RuntimeStatusMarker({
  block,
  icon,
  timeline,
}: {
  block: DisplayBlock;
  icon: React.ReactNode;
  timeline: boolean;
}) {
  const failed = block.outcome === 'failed';
  return (
    <span
      className={`${
        timeline
          ? 'absolute -left-[34px] top-0 z-10 flex h-5 w-5 rounded-full bg-[var(--runtime-process-surface)]'
          : 'flex h-6 w-6 rounded-md border bg-[var(--color-bg-primary)]'
      } shrink-0 items-center justify-center ${
        failed
          ? timeline
            ? 'text-[var(--color-error)]'
            : 'border-[var(--color-error)] text-[var(--color-error)]'
          : block.phase === 'open'
            ? timeline
              ? 'text-[var(--color-text-link)]'
              : 'border-[var(--color-border-focus)] text-[var(--color-text-link)]'
            : timeline
              ? 'text-[var(--color-text-tertiary)]'
              : 'border-[var(--color-border)] text-[var(--color-text-tertiary)]'
      }`}
    >
      {block.phase === 'open' ? (
        <Loader2 size={timeline ? 14 : 13} className="animate-spin" />
      ) : icon}
    </span>
  );
}

function RuntimeItem({
  icon,
  title,
  block,
  children,
  collapsible = false,
  timeline = true,
}: {
  icon: React.ReactNode;
  title: string;
  block: DisplayBlock;
  children?: React.ReactNode;
  collapsible?: boolean;
  timeline?: boolean;
}) {
  const failed = block.outcome === 'failed';
  const cancelled = block.outcome === 'cancelled';
  const status = statusLabel(block);
  const [expanded, setExpanded] = useState(false);
  const header = (
    <>
      <RuntimeStatusMarker block={block} icon={icon} timeline={timeline} />
      <span className="min-w-0 flex-1 truncate font-medium text-[var(--color-text-secondary)]">{title}</span>
      {status ? (
        <span
          className={`shrink-0 text-[11px] ${
            failed
              ? 'text-[var(--color-error)]'
              : cancelled
                ? 'text-[var(--color-warning)]'
                : 'text-[var(--color-text-link)]'
          }`}
        >
          {status}
        </span>
      ) : null}
      {collapsible && children ? (
        <ChevronRight
          size={14}
          className={`shrink-0 text-[var(--color-text-tertiary)] transition-transform ${expanded ? 'rotate-90' : ''}`}
        />
      ) : null}
    </>
  );
  return (
    <div
      className={`relative rounded-md px-2 py-2 text-sm transition-colors ${
        failed
          ? 'bg-[var(--color-error-bg)]'
          : !timeline
            ? 'border border-[var(--color-border)] bg-[var(--color-bg-secondary)]'
            : expanded
              ? 'bg-[var(--color-bg-primary)]'
              : 'hover:bg-[var(--color-bg-hover)]'
      }`}
    >
      {collapsible && children ? (
        <button
          type="button"
          aria-expanded={expanded}
          className={`relative flex min-h-6 w-full items-center gap-2 border-0 bg-transparent p-0 text-left ${failed ? 'text-[var(--color-error)]' : 'text-[var(--color-text-secondary)]'}`}
          onClick={() => setExpanded((current) => !current)}
        >
          {header}
        </button>
      ) : (
        <div className={`relative flex min-h-6 items-center gap-2 ${failed ? 'text-[var(--color-error)]' : 'text-[var(--color-text-secondary)]'}`}>
          {header}
        </div>
      )}
      {children && (!collapsible || expanded) ? (
        <div className={`mt-1.5 pl-0.5 ${failed ? 'text-[var(--color-error)]' : 'text-[var(--color-text-secondary)]'}`}>
          {children}
        </div>
      ) : null}
    </div>
  );
}

export function MessageBlockView() {
  return null;
}

export function TextBlockView({ block }: { block: DisplayBlock<string> }) {
  if (!block.data) return null;
  return (
    <div className="chat-message-content chat-message-content--assistant px-1 py-2 text-[var(--color-text-primary)]">
      <MarkdownContent content={block.data} />
      {block.phase === 'open' ? <span className="chat-stream-caret" aria-hidden="true" /> : null}
    </div>
  );
}

export function ProcessTextBlockView({ block }: { block: DisplayBlock<string> }) {
  if (!block.data) return null;
  return (
    <div
      className={`relative rounded-md px-2 py-2 text-sm transition-colors ${
        block.outcome === 'failed' ? 'bg-[var(--color-error-bg)]' : 'hover:bg-[var(--color-bg-hover)]'
      }`}
    >
      <div className="relative min-h-5">
        <RuntimeStatusMarker block={block} icon={<BrainCircuit size={14} />} timeline />
        <div className="max-h-64 overflow-auto text-xs text-[var(--color-text-secondary)]">
          <MarkdownContent content={block.data} />
        </div>
      </div>
    </div>
  );
}

export function ToolBlockView({ block }: { block: DisplayBlock<RecordData> }) {
  const name = text(block.data.name, 'Tool');
  const input = text(block.data.inputPreview);
  const output = text(block.data.outputPreview) || text(block.data.error);
  return (
    <RuntimeItem icon={toolIcon(name)} title={name} block={block} collapsible>
      {input ? <ToolDataPanel label="输入" value={input} /> : null}
      {output ? <ToolDataPanel label="输出" value={output} className={input ? 'mt-2' : ''} /> : null}
    </RuntimeItem>
  );
}

function toolIcon(name: string): React.ReactNode {
  const tokens = new Set(
    name
      .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter(Boolean),
  );
  const has = (...names: string[]) => names.some((value) => tokens.has(value));

  if (has('bash', 'shell', 'terminal')) return <Terminal size={14} />;
  if (has('read')) return <FileText size={14} />;
  if (has('edit', 'patch')) return <FilePenLine size={14} />;
  if (has('write', 'create')) return <FilePlus2 size={14} />;
  if (has('grep', 'search')) return <FileSearch size={14} />;
  if (has('glob', 'list')) return <FolderSearch size={14} />;
  if (has('http', 'web', 'fetch')) return <Globe2 size={14} />;
  if (has('ask')) return <MessageCircleQuestion size={14} />;
  if (has('delegate', 'subagent')) return <Bot size={14} />;
  if (has('plan', 'todo', 'task')) return <ListChecks size={14} />;
  if (has('skill')) return <Puzzle size={14} />;
  return <Wrench size={14} />;
}

function ToolDataPanel({ label, value, className = '' }: { label: string; value: string; className?: string }) {
  return (
    <div className={className}>
      <div className="mb-1 text-[11px] font-medium text-[var(--color-text-tertiary)]">{label}</div>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 font-mono text-xs leading-5 text-[var(--color-text-secondary)]">
        {formatToolTextValue(value)}
      </pre>
    </div>
  );
}

function formatToolTextValue(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

export function SkillBlockView({ block }: { block: DisplayBlock<RecordData> }) {
  return (
    <RuntimeItem
      icon={<Puzzle size={14} />}
      title={text(block.data.name, text(block.data.id, 'Skill'))}
      block={block}
    />
  );
}

export function ApprovalBlockView({ block }: { block: DisplayBlock<RecordData> }) {
  const actions = useContext(RuntimeBlockActionsContext);
  const request = toHitlRequest(block);
  const askUserToolCall = request?.toolCalls.find((toolCall) => (
    isAskUserToolName(toolCall.rawName || toolCall.name)
  ));
  if (block.phase === 'open' && request && askUserToolCall) {
    const clarification = buildClarificationRequestFromAskUser(askUserToolCall);
    return (
      <RuntimeItem icon={<MessageCircleQuestion size={14} />} title="等待回答" block={block}>
        <div className="text-xs font-medium text-[var(--color-text-secondary)]">{clarification.title}</div>
        {clarification.description ? (
          <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">{clarification.description}</div>
        ) : null}
      </RuntimeItem>
    );
  }
  if (block.phase === 'open' && request && actions?.onResolveInteraction) {
    return <HitlApprovalBlock request={request} onResolve={actions.onResolveInteraction} />;
  }
  return (
    <RuntimeItem
      icon={<CheckCircle2 size={14} />}
      title={text(block.data.title, '人工确认')}
      block={block}
    />
  );
}

export function PlanBlockView({ block }: { block: DisplayBlock<RecordData> }) {
  const steps = Array.isArray(block.data.steps) ? block.data.steps : [];
  const status = text(block.data.status, block.phase === 'open' ? 'drafting' : 'completed');
  const revision = Number(block.data.revision || 0);
  const completedCount = steps.filter((item) => (
    item && typeof item === 'object' && !Array.isArray(item)
    && ['completed', 'skipped'].includes(text((item as RecordData).status))
  )).length;
  const statusText: Record<string, string> = {
    drafting: '正在起草',
    revising: '正在修改',
    review_waiting: '等待确认',
    approved: '已确认',
    executing: '执行中',
    replanning: '重新规划',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  };
  return (
    <RuntimeItem icon={<ListChecks size={14} />} title={text(block.data.title, '执行计划')} block={block}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
        <span className="rounded bg-[var(--color-bg-secondary)] px-2 py-1">Revision {revision}</span>
        <span>{statusText[status] || status}</span>
        {steps.length > 0 ? <span className="ml-auto">{completedCount} / {steps.length}</span> : null}
      </div>
      {text(block.data.markdown) ? (
        <div className="mb-3 max-h-72 overflow-auto rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
          <MarkdownContent content={text(block.data.markdown)} />
        </div>
      ) : null}
      {steps.length ? (
        <div className="space-y-1.5">
          {steps.slice(0, 20).map((item, index) => {
            const step = item && typeof item === 'object' && !Array.isArray(item) ? item as RecordData : {};
            const stepStatus = text(step.status, 'pending');
            const StepIcon = stepStatus === 'completed' || stepStatus === 'skipped'
              ? CheckCircle2
              : stepStatus === 'running'
                ? Loader2
                : stepStatus === 'failed'
                  ? AlertCircle
                  : stepStatus === 'pending'
                    ? Circle
                    : CircleDot;
            return (
              <div key={text(step.id, String(index))} className="flex items-start gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-[var(--color-bg-hover)]">
                <StepIcon
                  size={13}
                  className={`mt-0.5 shrink-0 ${stepStatus === 'failed' ? 'text-[var(--color-error)]' : 'text-[var(--color-text-tertiary)]'} ${stepStatus === 'running' ? 'animate-spin' : ''}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="text-[var(--color-text-primary)]">{text(step.title, `步骤 ${index + 1}`)}</div>
                  {text(step.result) || text(step.evidence) ? (
                    <div className="mt-0.5 line-clamp-2 text-[var(--color-text-tertiary)]">{text(step.result, text(step.evidence))}</div>
                  ) : null}
                </div>
                <span className={stepStatus === 'failed' ? 'text-[var(--color-error)]' : 'text-[var(--color-text-tertiary)]'}>
                  {statusText[stepStatus] || stepStatus}
                </span>
              </div>
            );
          })}
        </div>
      ) : null}
    </RuntimeItem>
  );
}

export function ArtifactBlockView({ block }: { block: DisplayBlock<RecordData> }) {
  const actions = useContext(RuntimeBlockActionsContext);
  const files = artifactFiles(block.data);
  return (
    <RuntimeItem
      icon={<FileText size={14} />}
      title={files.length > 1 ? `已生成 ${files.length} 个文件` : files[0]?.name || '生成文件'}
      block={block}
      collapsible
      timeline={false}
    >
      {files.length ? (
        <div className="divide-y divide-[var(--color-border)]">
          {files.map((file) => {
            const downloadPath = downloadableArtifactPath(file.path, actions?.sessionId);
            const downloadUrl = actions?.sessionId && downloadPath
              ? resolveApiUrl(`/api/tools/files/download?sessionId=${encodeURIComponent(actions.sessionId)}&path=${encodeURIComponent(downloadPath)}`)
              : '';
            return (
              <div key={file.path} className="flex min-w-0 items-center gap-3 py-2">
                <FileText size={16} className="shrink-0 text-[var(--color-text-tertiary)]" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-[var(--color-text-primary)]" title={file.name}>
                    {file.name}
                  </div>
                  <div className="truncate text-xs text-[var(--color-text-tertiary)]" title={file.path}>
                    {file.extension ? `${file.extension} 文件` : file.path}
                  </div>
                </div>
                {downloadUrl ? (
                  <a
                    href={downloadUrl}
                    download={file.name}
                    title={`下载 ${file.name}`}
                    aria-label={`下载 ${file.name}`}
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                  >
                    <Download size={16} />
                  </a>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </RuntimeItem>
  );
}

type ArtifactFile = { path: string; name: string; extension: string };

function artifactFiles(data: RecordData): ArtifactFile[] {
  const files = Array.isArray(data.files) ? data.files.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
    const value = item as RecordData;
    const path = text(value.path);
    if (!path) return [];
    return [{
      path,
      name: text(value.name, fileNameFromPath(path)),
      extension: text(value.extension),
    }];
  }) : [];
  if (files.length) return files;

  const path = text(data.path, text(data.downloadPath, text(data.filePath)));
  return path ? [{ path, name: text(data.name, fileNameFromPath(path)), extension: text(data.extension) }] : [];
}

function fileNameFromPath(path: string): string {
  return path.replaceAll('\\', '/').split('/').filter(Boolean).at(-1) || '生成文件';
}

function downloadableArtifactPath(path: string, sessionId?: string): string {
  if (!sessionId) return '';
  const normalized = path.replaceAll('\\', '/');
  const marker = `.agent-workstation/sessions/${sessionId}/`;
  const markerIndex = normalized.indexOf(marker);
  if (markerIndex >= 0) return normalized.slice(markerIndex);
  return normalized.startsWith('/') ? '' : normalized;
}

export function ErrorBlockView({ block }: { block: DisplayBlock<RecordData> }) {
  return (
    <RuntimeItem icon={<AlertCircle size={14} />} title="执行失败" block={block} timeline={false}>
      <div className="text-sm text-[var(--color-error)]">{text(block.data.message, '未知错误')}</div>
    </RuntimeItem>
  );
}

export function SubagentBlockView({ block }: { block: DisplayBlock<RecordData> }) {
  const status = text(block.data.status, block.phase === 'closed' ? 'completed' : 'running');
  const deliveryStatus = text(block.data.deliveryStatus, 'none');
  const statusText: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    waiting: '等待确认',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    interrupted: '已中断',
  };
  const deliveryText: Record<string, string> = {
    pending: '等待主 Agent读取',
    delivered: '已读取，等待消费',
    consumed: '主 Agent已消费',
  };
  return (
    <RuntimeItem
      icon={<Bot size={14} />}
      title={`Subagent · ${text(block.data.subagentName, text(block.data.name, 'agent'))}`}
      block={block}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-secondary)]">
        <span>{statusText[status] || status}</span>
        {deliveryText[deliveryStatus] ? <span>· {deliveryText[deliveryStatus]}</span> : null}
      </div>
      {text(block.data.taskPreview, text(block.data.task)) ? (
        <div className="mt-2 line-clamp-3 text-xs">{text(block.data.taskPreview, text(block.data.task))}</div>
      ) : null}
      {text(block.data.resultPreview) || text(block.data.summary) ? (
        <div className="mt-2 border-t border-[var(--color-border)] pt-2 text-xs">
          {text(block.data.resultPreview, text(block.data.summary))}
        </div>
      ) : null}
    </RuntimeItem>
  );
}

export function TeamBlockView({ block }: { block: DisplayBlock<RecordData> }) {
  const counts = block.data.counts && typeof block.data.counts === 'object' && !Array.isArray(block.data.counts)
    ? block.data.counts as RecordData
    : {};
  return (
    <RuntimeItem icon={<Users size={14} />} title="Team 协作" block={block}>
      <div className="text-xs">
        {Number(counts.succeeded || 0)}/{Number(counts.tasks || 0)} 个任务完成
      </div>
    </RuntimeItem>
  );
}
