import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleDashed,
  CircleX,
  Copy,
  FileText,
  ListChecks,
  Loader2,
} from 'lucide-react';
import { Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import { copyTextToClipboard } from './chatUiUtils';
import type { ChatPlanBlock } from './chatDisplayTypes';

type PlanStep = ChatPlanBlock['steps'][number];

function planStatusLabel(status: string): string {
  if (status === 'completed') return '计划已生成';
  if (status === 'failed') return '规划失败';
  if (status === 'running') return '正在规划';
  return '计划';
}

function planStatusColor(status: string): string {
  if (status === 'failed') return 'danger';
  if (status === 'completed') return 'success';
  if (status === 'running') return 'blue';
  return 'orange';
}

function stepStatusLabel(status: string): string {
  if (status === 'completed') return '完成';
  if (status === 'failed') return '失败';
  if (status === 'running') return '进行中';
  if (status === 'skipped') return '跳过';
  return '待处理';
}

function stepStatusColor(status: string): string {
  if (status === 'failed') return 'danger';
  if (status === 'completed') return 'success';
  if (status === 'running') return 'blue';
  if (status === 'skipped') return 'transparent';
  return 'orange';
}

function stepStatusIcon(status: string) {
  if (status === 'completed') return <CheckCircle2 size={15} className="text-[var(--color-success)]" />;
  if (status === 'failed') return <CircleX size={15} className="text-[var(--color-error)]" />;
  if (status === 'running') return <Loader2 size={15} className="animate-spin text-[var(--color-primary)]" />;
  if (status === 'skipped') return <CircleDashed size={15} className="text-[var(--color-text-tertiary)]" />;
  return <Circle size={15} className="text-[var(--color-text-tertiary)]" />;
}

function planProgressLabel(block: ChatPlanBlock): string {
  if (block.steps.length === 0) {
    if (block.status === 'running') return '草稿生成中';
    if (block.status === 'completed') return '已生成';
    return String(block.status || '草稿');
  }
  const completedCount = block.steps.filter((step) => {
    const status = String(step.status || '');
    return status === 'completed' || status === 'skipped';
  }).length;
  const runningIndex = block.steps.findIndex((step) => String(step.status || '') === 'running');
  if (completedCount >= block.steps.length) {
    return `${completedCount}/${block.steps.length} 完成`;
  }
  if (runningIndex >= 0) {
    return `第 ${runningIndex + 1}/${block.steps.length} 步`;
  }
  return `${block.steps.length} 个步骤`;
}

export function PlanDisplayBlock({
  block,
  compact = false,
}: {
  block: ChatPlanBlock;
  compact?: boolean;
}) {
  const shouldOpenDraft = block.status === 'running' && block.steps.length === 0 && Boolean(block.markdown);
  const [markdownExpanded, setMarkdownExpanded] = useState(shouldOpenDraft);
  const previousStatusRef = useRef(block.status);

  useEffect(() => {
    if (shouldOpenDraft) {
      setMarkdownExpanded(true);
    }
    if (previousStatusRef.current !== 'completed' && block.status === 'completed') {
      setMarkdownExpanded(false);
    }
    previousStatusRef.current = block.status;
  }, [block.status, shouldOpenDraft]);

  return (
    <section className={compact
      ? 'overflow-hidden bg-[var(--color-bg-primary)]'
      : 'overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] shadow-sm'}
    >
      <PlanHeader block={block} />

      {block.steps.length > 0 && <PlanStepList steps={block.steps} />}

      {block.markdown && (
        <PlanMarkdownPreview
          markdown={block.markdown}
          expanded={markdownExpanded}
          running={block.status === 'running'}
          onToggle={() => setMarkdownExpanded((value) => !value)}
        />
      )}
    </section>
  );
}

function PlanHeader({ block }: { block: ChatPlanBlock }) {
  return (
    <div className="border-b border-[var(--color-border)] px-3 py-2.5">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <ListChecks size={16} className="shrink-0 text-[var(--color-text-secondary)]" />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {planStatusLabel(block.status)}
            </div>
            <PlanFileMeta planFile={block.planFile} />
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Tag size="small" color={planStatusColor(block.status)}>
            {block.status === 'completed' ? 'completed' : block.status || 'plan'}
          </Tag>
          <span className="rounded-full bg-[var(--color-bg-tertiary)] px-2 py-0.5 text-xs text-[var(--color-text-secondary)]">
            {planProgressLabel(block)}
          </span>
        </div>
      </div>
    </div>
  );
}

function PlanFileMeta({ planFile }: { planFile: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    if (!planFile) return;
    await copyTextToClipboard(planFile);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }, [planFile]);

  if (!planFile) return null;
  return (
    <div className="mt-0.5 flex min-w-0 items-center gap-1 text-[11px] text-[var(--color-text-tertiary)]">
      <FileText size={12} className="shrink-0" />
      <span className="truncate font-mono" title={planFile}>{planFile}</span>
      <button
        type="button"
        title="复制计划路径"
        aria-label="复制计划路径"
        onClick={handleCopy}
        className="ml-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
    </div>
  );
}

function PlanStepList({ steps }: { steps: PlanStep[] }) {
  return (
    <ol className="divide-y divide-[var(--color-border)] px-3">
      {steps.map((step, index) => {
        const status = String(step.status || 'pending');
        const description = String(step.description || '');
        const result = String(step.result || step.evidence || '');
        return (
          <li key={String(step.id || index)} className="flex gap-3 py-2.5">
            <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center">
              {stepStatusIcon(status)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-start justify-between gap-2">
                <div className="min-w-0 text-sm font-medium leading-5 text-[var(--color-text-primary)]">
                  <span className="mr-1 text-xs font-normal text-[var(--color-text-tertiary)]">
                    {Number(step.stepIndex ?? index) + 1}.
                  </span>
                  <span className="break-words">{String(step.title || 'Untitled task')}</span>
                </div>
                <Tag size="small" color={stepStatusColor(status)}>
                  {stepStatusLabel(status)}
                </Tag>
              </div>
              {description && (
                <div className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--color-text-tertiary)]">
                  {description}
                </div>
              )}
              {result && (
                <div className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--color-text-secondary)]">
                  {result}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function PlanMarkdownPreview({
  markdown,
  expanded,
  running,
  onToggle,
}: {
  markdown: string;
  expanded: boolean;
  running: boolean;
  onToggle: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    if (!expanded || !running) return;
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
    const frame = window.requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [expanded, markdown, running]);

  return (
    <div className="border-t border-[var(--color-border)]">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition hover:bg-[var(--color-bg-hover)]"
      >
        <span className="text-xs font-medium text-[var(--color-text-secondary)]">完整计划</span>
        {expanded ? (
          <ChevronDown size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
        )}
      </button>
      {expanded && (
        <div
          ref={scrollRef}
          className="max-h-72 overflow-auto px-3 pb-3 text-xs leading-6 text-[var(--color-text-secondary)]"
        >
          <MarkdownContent content={markdown} />
        </div>
      )}
    </div>
  );
}
