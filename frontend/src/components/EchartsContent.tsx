/**
 * 消息正文渲染：Markdown 为主，闭合的 ```echarts 围栏按需换成图表。
 * 解析规则见 `echartsMarkdown.ts`；echarts 运行时懒加载，不含图表的消息不会触发下载。
 */

import { lazy, Suspense } from 'react';
import { MarkdownContent } from './MarkdownContent';
import { splitEchartsSegments } from './echartsMarkdown';

const EchartsChart = lazy(() => import('./EchartsChart'));

interface EchartsContentProps {
  content: string;
  streaming?: boolean;
  className?: string;
}

function ChartPlaceholder() {
  return (
    <div
      role="status"
      aria-label="图表加载中"
      style={{ width: '100%', height: 320, margin: '12px 0' }}
      className="rounded-md bg-[var(--color-bg-secondary)]"
    />
  );
}

export function EchartsContent({ content, streaming = false, className = '' }: EchartsContentProps) {
  const segments = splitEchartsSegments(content);
  if (segments.length === 1 && segments[0].kind === 'markdown') {
    return <MarkdownContent content={content} streaming={streaming} className={className} />;
  }
  const lastIndex = segments.length - 1;
  return (
    <div className={className}>
      {segments.map((segment, index) => {
        if (segment.kind === 'chart') {
          return (
            <Suspense key={`chart-${index}`} fallback={<ChartPlaceholder />}>
              <EchartsChart option={segment.option} />
            </Suspense>
          );
        }
        if (!segment.text.trim()) return null;
        // 只有末尾片段仍在流式增长，前面的片段已经定型。
        return (
          <MarkdownContent
            key={`md-${index}`}
            content={segment.text}
            streaming={streaming && index === lastIndex}
          />
        );
      })}
    </div>
  );
}
