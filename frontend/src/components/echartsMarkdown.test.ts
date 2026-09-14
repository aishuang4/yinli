/** `yinli-charts` Skill 合同的解析回归。 */

import { describe, expect, it } from 'vitest';
import { splitEchartsSegments, toSafeEchartsOption } from './echartsMarkdown';

const BAR = JSON.stringify({
  xAxis: { type: 'category', data: ['1月', '2月'] },
  yAxis: { type: 'value' },
  series: [{ type: 'bar', data: [1, 2] }],
});

function fence(lang: string, body: string): string {
  return `\`\`\`${lang}\n${body}\n\`\`\``;
}

describe('splitEchartsSegments', () => {
  it('纯 Markdown 原样返回单个片段', () => {
    expect(splitEchartsSegments('# 标题\n\n正文')).toEqual([{ kind: 'markdown', text: '# 标题\n\n正文' }]);
  });

  it('闭合的 echarts 围栏渲染为图表，前后 Markdown 保留', () => {
    const content = `前言\n\n${fence('echarts', BAR)}\n\n结论`;
    const segments = splitEchartsSegments(content);
    expect(segments.map((segment) => segment.kind)).toEqual(['markdown', 'chart', 'markdown']);
    expect(segments[0]).toEqual({ kind: 'markdown', text: '前言\n\n' });
    expect(segments[2]).toEqual({ kind: 'markdown', text: '\n\n结论' });
  });

  it('echarts-json 别名同样生效', () => {
    const segments = splitEchartsSegments(fence('echarts-json', BAR));
    expect(segments).toHaveLength(1);
    expect(segments[0].kind).toBe('chart');
  });

  it('普通 json 与无标签围栏不渲染', () => {
    expect(splitEchartsSegments(fence('json', BAR))[0].kind).toBe('markdown');
    expect(splitEchartsSegments(fence('', BAR))[0].kind).toBe('markdown');
  });

  it('未闭合围栏在流式期间保持为 Markdown', () => {
    const streaming = `说明\n\`\`\`echarts\n${BAR.slice(0, 40)}`;
    expect(splitEchartsSegments(streaming)).toEqual([{ kind: 'markdown', text: streaming }]);
  });

  it('非法 JSON 与不受支持的 option 回退为代码块', () => {
    expect(splitEchartsSegments(fence('echarts', '{ not json'))[0].kind).toBe('markdown');
    const withDataset = JSON.stringify({ dataset: {}, series: [{ type: 'bar' }] });
    expect(splitEchartsSegments(fence('echarts', withDataset))[0].kind).toBe('markdown');
    const unsupportedType = JSON.stringify({ series: [{ type: 'radar' }] });
    expect(splitEchartsSegments(fence('echarts', unsupportedType))[0].kind).toBe('markdown');
  });

  it('多个图表围栏各自独立', () => {
    const content = `${fence('echarts', BAR)}\n中间\n${fence('echarts', BAR)}`;
    expect(splitEchartsSegments(content).map((segment) => segment.kind)).toEqual(['chart', 'markdown', 'chart']);
  });
});

describe('toSafeEchartsOption', () => {
  it('拒绝非对象、空 series 与 graphic / calendar / custom', () => {
    expect(toSafeEchartsOption(null)).toBeNull();
    expect(toSafeEchartsOption([])).toBeNull();
    expect(toSafeEchartsOption({ series: [] })).toBeNull();
    expect(toSafeEchartsOption({ graphic: {}, series: [{ type: 'line' }] })).toBeNull();
    expect(toSafeEchartsOption({ calendar: {}, series: [{ type: 'line' }] })).toBeNull();
    expect(toSafeEchartsOption({ custom: {}, series: [{ type: 'line' }] })).toBeNull();
  });

  it('接受 bar / line / pie / scatter', () => {
    for (const type of ['bar', 'line', 'pie', 'scatter']) {
      expect(toSafeEchartsOption({ series: [{ type, data: [] }] })).not.toBeNull();
    }
  });
});
