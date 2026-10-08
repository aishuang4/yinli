/** Chat Runtime Block 的领域 renderer。 */

import { Children, useContext, useEffect } from 'react';
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Circle,
  CircleDot,
  Download,
  FileText,
  ListChecks,
  Loader2,
  MessageCircleQuestion,
  Puzzle,
} from 'lucide-react';
import { toolIcon } from './toolIcon';
import { MarkdownContent } from '../../components/MarkdownContent';
import { EchartsContent } from '../../components/EchartsContent';
import { HitlApprovalBlock } from '../chat/HumanInteractionBlocks';
import { useSmoothStreamingText } from '../chat/useSmoothStreamingText';
import {
  buildClarificationRequestFromAskUser,
  isAskUserToolName,
  toHitlRequest,
} from '../chat/humanInteractionUtils';
import { resolveApiUrl } from '../../utils/apiUrl';
import { artifactDownloadUrl, downloadableArtifactPath } from '../file-preview/filePreview';
import type { DisplayBlock } from './protocol';
import { toolDetail } from './processTrace';
import { RuntimeAnimationContext } from './RuntimeAnimationContext';
import { RuntimeBlockActionsContext } from './runtimeBlockActions';

export type RecordData = Record<string, unknown>;

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function statusLabel(block: DisplayBlock): string {
  if (block.phase === 'open') return '进行中';
  if (block.outcome === 'failed') return '失败';
  if (block.outcome === 'cancelled') return '已中断';
  return '';
}

function RuntimeItem({
  icon,
  title,
  block,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  block: DisplayBlock;
  children?: React.ReactNode;
}) {
  const failed = block.outcome === 'failed';
  const cancelled = block.outcome === 'cancelled';
  const open = block.phase === 'open';
  const status = statusLabel(block);
  const body = Children.toArray(children).filter(Boolean);
  return (
    <div className={`py-1 text-sm ${failed ? 'text-[var(--color-error)]' : 'text-[var(--color-text-secondary)]'}`}>
      <div className="flex min-h-6 items-center gap-2">
        <span
          className={`flex h-4 w-4 shrink-0 items-center justify-center ${
            failed
              ? 'text-[var(--color-error)]'
              : open
                ? 'text-[var(--color-text-secondary)]'
                : 'text-[var(--color-text-tertiary)]'
          }`}
        >
          {open ? <Loader2 size={14} className="animate-spin" /> : icon}
        </span>
        <span className="min-w-0 flex-1 truncate">{title}</span>
        {status ? (
          <span
            className={`shrink-0 text-[11px] ${
              failed
                ? 'text-[var(--color-error)]'
                : cancelled
                  ? 'text-[var(--color-warning)]'
                  : 'text-[var(--color-text-tertiary)]'
            }`}
          >
            {status}
          </span>
        ) : null}
      </div>
      {body.length ? <div className="mt-1.5 pl-6">{body}</div> : null}
    </div>
  );
}

export function MessageBlockView() {
  return null;
}

export function TextBlockView({ block }: { block: DisplayBlock<string> }) {
  const reportAnimation = useContext(RuntimeAnimationContext);
  // 折叠切换会换父节点。已收到的正文直接显示，只补这次挂载之后新到的字。
  const visible = useSmoothStreamingText(block.data, block.phase === 'open', {
    animateOnMount: false,
    animateStreamingOnMount: false,
    charsPerSecond: 96,
    maxCharsPerFrame: 18,
  });
  useEffect(() => {
    reportAnimation?.(visible.isAnimating);
  }, [reportAnimation, visible.isAnimating]);
  if (!block.data && !visible.content) return null;
  return (
    <div className="chat-message-content chat-message-content--assistant px-1 py-2 text-[var(--color-text-primary)]">
      <EchartsContent content={visible.content} streaming={visible.isAnimating} />
    </div>
  );
}

export function ProcessTextBlockView({
  block,
  hideLabel = false,
}: {
  block: DisplayBlock;
  hideLabel?: boolean;
}) {
  const content = text(block.data);
  if (!content) return null;
  const label = block.kind === 'thinking' ? '思考过程' : '过程';
  return (
    <div className="py-1 text-sm">
      {hideLabel ? null : <div className="mb-1 text-[13px] text-[var(--color-text-tertiary)]">{label}</div>}
      <div className="text-xs leading-5 text-[var(--color-text-secondary)]">
        <MarkdownContent content={content} />
      </div>
    </div>
  );
}

export function ToolBlockView({ block }: { block: DisplayBlock<RecordData> }) {
  const name = text(block.data.name, 'Tool');
  const detail = toolDetail(block);
  const open = block.phase === 'open';
  return (
    <div className="flex items-start gap-2 px-1 py-1 text-xs leading-5 text-[var(--color-text-tertiary)]">
      <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">
        {open ? <Loader2 size={14} className="animate-spin" /> : toolIcon(name)}
      </span>
      <span className="min-w-0">
        <span className="block break-words">{detail.title}</span>
        {detail.note ? <span className="mt-0.5 block break-words">{detail.note}</span> : null}
      </span>
    </div>
  );
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
  if (!files.length) return null;
  return (
    <RuntimeItem
      icon={<FileText size={14} />}
      title={files.length > 1 ? `已生成 ${files.length} 个文件` : files[0]?.name || '生成文件'}
      block={block}
    >
      {files.length ? (
        <div className="divide-y divide-[var(--color-border)]">
          {files.map((file) => {
            const downloadPath = downloadableArtifactPath(file.path, actions?.sessionId);
            const downloadHref = actions?.sessionId && downloadPath
              ? resolveApiUrl(artifactDownloadUrl(actions.sessionId, downloadPath))
              : '';
            const canPreview = Boolean(actions?.sessionId && downloadPath && actions.onOpenPreview);
            const selected = Boolean(canPreview && actions?.previewPath === downloadPath);
            const openPreview = () => {
              if (!canPreview || !actions?.sessionId || !actions.onOpenPreview) return;
              actions.onOpenPreview({
                sessionId: actions.sessionId,
                path: downloadPath,
                name: file.name,
              });
            };
            const fileMeta = (
              <>
                <FileText size={16} className="shrink-0 text-[var(--color-text-tertiary)]" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-[var(--color-text-primary)]" title={file.name}>
                    {file.name}
                  </div>
                  <div className="truncate text-xs text-[var(--color-text-tertiary)]" title={file.path}>
                    {file.extension ? `${file.extension} 文件` : file.path}
                  </div>
                </div>
              </>
            );
            return (
              <div
                key={file.path}
                className={`flex min-w-0 items-center gap-2 rounded-md px-1 ${selected ? 'bg-[var(--color-bg-secondary)]' : canPreview ? 'hover:bg-[var(--color-bg-hover)]' : ''}`}
              >
                {canPreview ? (
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 items-center gap-3 py-2 text-left"
                    onClick={openPreview}
                    aria-pressed={selected}
                    aria-label={`预览 ${file.name}`}
                  >
                    {fileMeta}
                  </button>
                ) : (
                  <div className="flex min-w-0 flex-1 items-center gap-3 py-2">
                    {fileMeta}
                  </div>
                )}
                {downloadHref ? (
                  <a
                    href={downloadHref}
                    download={file.name}
                    title={`下载 ${file.name}`}
                    aria-label={`下载 ${file.name}`}
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                    onClick={(event) => event.stopPropagation()}
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
  if (files.length) return visibleArtifactFiles(files);

  const path = text(data.path, text(data.downloadPath, text(data.filePath)));
  const single = path ? [{ path, name: text(data.name, fileNameFromPath(path)), extension: text(data.extension) }] : [];
  return visibleArtifactFiles(single);
}

function visibleArtifactFiles(files: ArtifactFile[]): ArtifactFile[] {
  const officeStems = new Set(
    files
      .filter((file) => /\.(docx|xlsx|pptx)$/i.test(file.name))
      .map((file) => fileStem(file.name)),
  );
  return files.filter((file) => {
    const name = file.name.toLowerCase();
    const path = file.path.replaceAll('\\', '/').toLowerCase();
    if (!name || name.startsWith('.') || name.startsWith('tool_result')) return false;
    if (name === 'source.md') return false;
    if (path.split('/').some((part) => ['render', 'tmp', 'temp', '_scratch', 'subagents', 'team'].includes(part))) {
      return false;
    }
    if (officeStems.has(fileStem(file.name)) && /\.(pdf|png|jpe?g|webp)$/i.test(name)) return false;
    return true;
  });
}

function fileStem(name: string): string {
  return name.replace(/\.[^.]+$/, '');
}

function fileNameFromPath(path: string): string {
  return path.replaceAll('\\', '/').split('/').filter(Boolean).at(-1) || '生成文件';
}

export function ErrorBlockView({ block }: { block: DisplayBlock<RecordData> }) {
  return (
    <RuntimeItem icon={<AlertCircle size={14} />} title="执行失败" block={block}>
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
