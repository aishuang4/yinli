/** Chat 顶栏：当前会话上下文占用与压缩阈值。 */

import { Tooltip } from '@/components/design';
import {
  resolveContextUsedTokens,
  type ContextUsageData,
} from './contextUsage';

export type { ContextUsageData } from './contextUsage';
export {
  resolveContextUsedTokens,
  selectContextUsage,
  selectLatestContextUsage,
} from './contextUsage';

function formatCount(value: number): string {
  if (value >= 10_000) {
    return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}k`;
  }
  return String(value);
}

export function ChatContextMeter({ usage }: { usage: ContextUsageData | null }) {
  if (!usage) {
    return null;
  }
  const { used, estimated } = resolveContextUsedTokens(usage);
  const windowSize = Number(usage.contextWindow || 0);
  const trigger = Number(usage.triggerTokens || 0);
  const triggerRatio = Number(usage.triggerRatio || 0.8);
  const compressed = Number(usage.compressed || 0) === 1;
  const overTrigger = Number(usage.overTrigger || 0) === 1 || (trigger > 0 && used >= trigger);
  if (!used && !windowSize) {
    return null;
  }
  const ratio = windowSize > 0 ? Math.min(100, (used / windowSize) * 100) : null;
  const ratioLabel = ratio == null
    ? ''
    : ratio > 0 && ratio < 1
      ? '<1%'
      : `${Math.round(ratio)}%`;
  const triggerPct = windowSize > 0 && trigger > 0
    ? Math.min(100, (trigger / windowSize) * 100)
    : null;
  const label = windowSize > 0
    ? `${estimated ? '约 ' : ''}${formatCount(used)} / ${formatCount(windowSize)}`
    : `${estimated ? '约 ' : ''}${formatCount(used)}`;
  const status = compressed
    ? '已压缩'
    : overTrigger
      ? '已达压缩阈值'
      : '未压缩';
  const warn = compressed || overTrigger;
  const title = [
    estimated ? `估算占用 ${used} tokens（字符折半，未额外调用 tokenizer）` : `本轮提示 ${used} tokens`,
    windowSize ? `模型窗口 ${windowSize}` : '未读取到模型窗口，按 AgentScope 默认对照',
    trigger ? `压缩阈值 ${formatCount(trigger)}（窗口的 ${Math.round(triggerRatio * 100)}%）` : '',
    compressed ? '当前会话已有压缩摘要' : '尚未触发上下文压缩',
    usage.contextMessages != null ? `会话消息 ${usage.contextMessages} 条` : '',
  ].filter(Boolean).join('\n');

  return (
    <Tooltip title={<span className="whitespace-pre-line">{title}</span>} placement="bottom" mode="dark">
      <div
        className={`chat-workbench-context-meter${warn ? ' chat-workbench-context-meter--warn' : ''}`}
        aria-label={`上下文占用 ${label}${ratioLabel ? `，${ratioLabel}` : ''}，${status}`}
      >
        <div className="chat-workbench-context-meter__row">
          <span className="truncate">上下文 {label}{ratioLabel ? ` · ${ratioLabel}` : ''}</span>
          <span className="shrink-0">{status}</span>
        </div>
        {ratio != null ? (
          <div
            className="chat-workbench-context-meter__track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(ratio)}
          >
            <div className="chat-workbench-context-meter__fill" style={{ width: `${ratio}%` }} />
            {triggerPct != null ? (
              <div className="chat-workbench-context-meter__trigger" style={{ left: `${triggerPct}%` }} />
            ) : null}
          </div>
        ) : null}
      </div>
    </Tooltip>
  );
}
