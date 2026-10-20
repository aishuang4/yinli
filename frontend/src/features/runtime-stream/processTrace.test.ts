/** 终态过程摘要：连续的网页抓取收成一行，正文留在时间顺序里。 */

import { describe, expect, it } from 'vitest';
import { emptyDisplaySnapshot, type DisplayBlock, type DisplaySnapshot } from './protocol';
import { activityLabel, bashToolView, elapsedSeconds, formatShellCommand, messageStatusText, segmentTrace, shellSummary, toolArgumentText, toolCallName, toolDetail, webSearchDetail } from './processTrace';

function block(partial: {
  id: string;
  kind: string;
  phase?: DisplayBlock['phase'];
  outcome?: DisplayBlock['outcome'];
  data?: unknown;
}): DisplayBlock {
  const phase = partial.phase ?? 'closed';
  return {
    id: partial.id,
    kind: partial.kind,
    dataVersion: 1,
    phase,
    outcome: phase === 'open' ? undefined : partial.outcome ?? 'completed',
    data: partial.data ?? {},
  };
}

function snapshot(blocks: DisplayBlock[]): DisplaySnapshot {
  const next = emptyDisplaySnapshot();
  next.order = blocks.map((item) => item.id);
  next.blocksById = Object.fromEntries(blocks.map((item) => [item.id, item]));
  return next;
}

describe('segmentTrace', () => {
  it('把连续网页抓取收成已搜索，正文和思考留在原位', () => {
    const tree = snapshot([
      block({ id: 'think', kind: 'thinking', data: '先看天气来源' }),
      block({ id: 'text-1', kind: 'text', data: '我查一下武汉今天的天气信息。' }),
      block({
        id: 'web-1',
        kind: 'tool',
        outcome: 'failed',
        data: { name: 'WebFetch', inputPreview: '{"url":"https://wttr.in/Wuhan?format=j1"}' },
      }),
      block({
        id: 'web-2',
        kind: 'tool',
        data: { name: 'WebFetch', inputPreview: '{"url":"https://www.weather.com.cn/weather/101200101.shtml"}' },
      }),
      block({ id: 'text-2', kind: 'text', data: '搜索结果有点不一致。' }),
      block({ id: 'bash', kind: 'tool', data: { name: 'Bash', inputPreview: '{"command":"curl wttr.in"}' } }),
      block({ id: 'approved', kind: 'approval', data: { title: '人工确认' } }),
    ]);
    expect(segmentTrace(tree, ['think', 'text-1', 'web-1', 'web-2', 'text-2', 'bash', 'approved'])).toEqual([
      { type: 'thinking', ids: ['think'] },
      { type: 'prose', id: 'text-1' },
      { type: 'activity', ids: ['web-1', 'web-2'], label: '已搜索 2 个网页' },
      { type: 'prose', id: 'text-2' },
      { type: 'activity', ids: ['bash'], label: '已执行 1 条命令' },
    ]);
  });

  it('展开后的网页行只保留地址和失败说明', () => {
    const failed = block({
      id: 'web',
      kind: 'tool',
      outcome: 'failed',
      data: {
        name: 'WebFetch',
        inputPreview: '{"url":"https://wttr.in/Wuhan?format=j1","max_chars":6000}',
        outputPreview: 'Error: WebFetch blocks this network target.',
      },
    });
    expect(toolCallName(failed)).toBe('WebFetch');
    expect(toolDetail(failed)).toEqual({
      title: 'wttr.in/Wuhan',
      note: 'Error: WebFetch blocks this network target.',
    });
    expect(activityLabel(snapshot([failed]), ['web'])).toBe('已搜索 1 个网页');
  });

  it('展开网页搜索时用标题和查询词，失败说明不进标题', () => {
    const failed = block({
      id: 'web-failed',
      kind: 'tool',
      outcome: 'failed',
      data: {
        name: 'WebFetch',
        inputPreview: '{"url":"https://wttr.in/Wuhan?format=j1","max_chars":6000}',
        outputPreview: 'Error: WebFetch blocks this network target.',
      },
    });
    const titled = block({
      id: 'web-title',
      kind: 'tool',
      data: {
        name: 'WebFetch',
        inputPreview: '{"url":"https://www.baidu.com/s?wd=%E6%AD%A6%E6%B1%89%E5%A4%A9%E6%B0%94"}',
        outputPreview: `{
          "url": "https://www.baidu.com/s?wd=%E6%AD%A6%E6%B1%89%E5%A4%A9%E6%B0%94",
          "final_url": "https://www.weather.com.cn/weather1d/101200101.shtml",
          "title": "武汉天气预报",
          "content": "很长且被截断`,
      },
    });
    const html = block({
      id: 'web-html',
      kind: 'tool',
      data: {
        name: 'HttpRequest',
        inputPreview: '{"query":"武汉天气 2026年9月30日 今天"}',
        outputPreview: '<html><title>光明网 &amp; 天气</title><body>正文</body></html>',
      },
    });
    expect(webSearchDetail([failed]).pages[0]).toEqual({
      title: 'wttr.in/Wuhan',
      url: 'https://wttr.in/Wuhan?format=j1',
      host: 'wttr.in',
    });
    expect(webSearchDetail([failed]).query).toBe('');
    expect(webSearchDetail([titled])).toEqual({
      query: '武汉天气',
      pages: [{
        title: '武汉天气预报',
        url: 'https://www.weather.com.cn/weather1d/101200101.shtml',
        host: 'www.weather.com.cn',
      }],
    });
    expect(webSearchDetail([html])).toMatchObject({
      query: '武汉天气 2026年9月30日 今天',
      pages: [{ title: '光明网 & 天气', host: '' }],
    });
    expect(webSearchDetail([
      block({ id: 'a', kind: 'tool', data: { name: 'WebSearch', inputPreview: '{"query":"甲"}' } }),
      block({ id: 'b', kind: 'tool', data: { name: 'WebSearch', inputPreview: '{"query":"乙"}' } }),
    ]).query).toBe('');
  });

  it('Bash 按连接符折行，引号里的分号保持原样', () => {
    const command = 'echo "=== 系统信息 ===" && sw_vers 2>/dev/null; echo "a && b; c"';
    expect(formatShellCommand(command)).toBe('echo "=== 系统信息 ==="\nsw_vers 2>/dev/null\necho "a && b; c"');
    expect(formatShellCommand('(sudo true || echo "跳过")')).toBe('(sudo true || echo "跳过")');
    expect(shellSummary(command)).toBe('系统信息');
    const tool = block({
      id: 'bash',
      kind: 'tool',
      outcome: 'failed',
      data: {
        name: 'Bash',
        inputPreview: JSON.stringify({ command }),
        error: 'Command blocked by Agent guardrails: sudo',
      },
    });
    expect(bashToolView(tool)).toEqual({
      summary: '系统信息',
      command: 'echo "=== 系统信息 ==="\nsw_vers 2>/dev/null\necho "a && b; c"',
      note: 'Command blocked by Agent guardrails: sudo',
    });
  });

  it('横线上的字跟着消息状态变', () => {
    expect(messageStatusText({ running: true, stopped: false, seconds: null })).toBe('运行中');
    expect(messageStatusText({ running: false, stopped: false, seconds: 18 })).toBe('用时 18秒');
    expect(messageStatusText({ running: false, stopped: false, seconds: null })).toBe('已完成');
    expect(messageStatusText({ running: false, stopped: true, seconds: 18 })).toBe('已中断 · 18秒');
    expect(messageStatusText({ running: false, stopped: true, seconds: null })).toBe('已中断');
  });

  it('工具结束后仍保留调用参数', () => {
    const tool = block({
      id: 'read-1',
      kind: 'tool',
      data: { name: 'Read', inputPreview: '{"file_path":"notes/today.md"}' },
    });
    expect(toolArgumentText(tool)).toBe('{\n  "file_path": "notes/today.md"\n}');
    expect(toolDetail(tool).title).toBe('notes/today.md');
  });

  it('用开始和结束时间算秒数', () => {
    expect(elapsedSeconds(
      '2026-09-30T06:07:28.044955+00:00',
      '2026-09-30T06:07:53.535750+00:00',
    )).toBe(25);
    expect(elapsedSeconds('', '2026-09-30T06:07:53+00:00')).toBeNull();
  });
});
