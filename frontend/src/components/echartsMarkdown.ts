/**
 * 助手回复中的 ECharts 围栏解析。
 *
 * 合同来自内置 Skill `yinli-charts`：
 * - 只认 ```echarts / ```echarts-json 围栏，普通 ```json 不渲染；
 * - 围栏未闭合时当代码块，闭合后才尝试解析；
 * - 载荷必须是纯 JSON 对象，series 非空且只允许 bar / line / pie / scatter；
 * - 不允许 dataset / graphic / calendar / custom。
 *
 * 本模块不依赖 React 与 echarts 运行时，便于单测和保持消息渲染路径轻量。
 */

import type { EChartsOption } from 'echarts';

export type EchartsSegment =
  | { kind: 'markdown'; text: string }
  | { kind: 'chart'; option: EChartsOption; raw: string };

const FENCE_PATTERN = /```(echarts|echarts-json)[ \t]*\r?\n([\s\S]*?)\r?\n?```/gi;
const ALLOWED_SERIES_TYPES = new Set(['bar', 'line', 'pie', 'scatter']);
const FORBIDDEN_KEYS = ['dataset', 'graphic', 'calendar', 'custom'] as const;

/** 快速判断是否可能含图表围栏，避免对每个流式增量都跑正则。 */
export function mayContainEcharts(content: string): boolean {
  return content.includes('```echarts');
}

/** 校验解析后的对象是否为受支持的 ECharts option。 */
export function toSafeEchartsOption(value: unknown): EChartsOption | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const option = value as Record<string, unknown>;
  if (FORBIDDEN_KEYS.some((key) => key in option)) return null;
  const series = option.series;
  if (!Array.isArray(series) || series.length === 0) return null;
  const typesValid = series.every((item) => {
    if (!item || typeof item !== 'object') return false;
    const type = (item as { type?: unknown }).type;
    return typeof type === 'string' && ALLOWED_SERIES_TYPES.has(type);
  });
  if (!typesValid) return null;
  return option as EChartsOption;
}

function parseFenceBody(body: string): EChartsOption | null {
  try {
    return toSafeEchartsOption(JSON.parse(body));
  } catch {
    return null;
  }
}

/** 把回复正文拆成 Markdown 与图表片段；未闭合或非法围栏保留为 Markdown 原文。 */
export function splitEchartsSegments(content: string): EchartsSegment[] {
  if (!mayContainEcharts(content)) return [{ kind: 'markdown', text: content }];
  const segments: EchartsSegment[] = [];
  let cursor = 0;
  for (const match of content.matchAll(FENCE_PATTERN)) {
    const start = match.index ?? 0;
    const raw = match[0];
    const option = parseFenceBody(match[2]);
    if (!option) continue;
    if (start > cursor) segments.push({ kind: 'markdown', text: content.slice(cursor, start) });
    segments.push({ kind: 'chart', option, raw });
    cursor = start + raw.length;
  }
  if (segments.length === 0) return [{ kind: 'markdown', text: content }];
  if (cursor < content.length) segments.push({ kind: 'markdown', text: content.slice(cursor) });
  return segments;
}
