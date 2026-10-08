/** 终态过程按时间切成正文、思考和一行操作摘要。 */

import type { DisplayBlock, DisplaySnapshot } from './protocol';

export type TraceSegment =
  | { type: 'prose'; id: string }
  | { type: 'thinking'; ids: string[] }
  | { type: 'activity'; ids: string[]; label: string }
  | { type: 'block'; id: string };

const THINKING_KINDS = new Set(['thinking', 'reasoning']);

export function segmentTrace(snapshot: DisplaySnapshot, ids: string[]): TraceSegment[] {
  const segments: TraceSegment[] = [];
  for (const id of ids) {
    const block = snapshot.blocksById[id];
    if (!block || isClosedApproval(block)) continue;
    if (block.kind === 'text') {
      if (!textData(block).trim()) continue;
      segments.push({ type: 'prose', id });
      continue;
    }
    if (THINKING_KINDS.has(block.kind)) {
      if (!textData(block).trim()) continue;
      const last = segments.at(-1);
      if (last?.type === 'thinking') last.ids.push(id);
      else segments.push({ type: 'thinking', ids: [id] });
      continue;
    }
    if (block.kind === 'tool') {
      const family = toolFamily(toolName(block));
      const last = segments.at(-1);
      if (last?.type === 'activity' && activityFamily(snapshot, last.ids) === family) {
        last.ids.push(id);
        last.label = activityLabel(snapshot, last.ids);
      } else {
        segments.push({ type: 'activity', ids: [id], label: activityLabel(snapshot, [id]) });
      }
      continue;
    }
    segments.push({ type: 'block', id });
  }
  return segments;
}

export function activityLabel(snapshot: DisplaySnapshot, ids: string[]): string {
  const blocks = ids
    .map((id) => snapshot.blocksById[id])
    .filter((block): block is DisplayBlock => Boolean(block));
  const count = blocks.length || 1;
  const open = blocks.some((block) => block.phase === 'open');
  const family = toolFamily(toolName(blocks[0]));
  if (family === 'web') return open ? '正在搜索网页' : `已搜索 ${count} 个网页`;
  if (family === 'read') return open ? '正在阅读文件' : `已阅读 ${count} 个文件`;
  if (family === 'search') return open ? '正在查找' : `已查找 ${count} 次`;
  if (family === 'bash') return open ? '正在执行命令' : `已执行 ${count} 条命令`;
  if (family === 'edit') return open ? '正在修改文件' : `已修改 ${count} 处`;
  const name = toolName(blocks[0]) || '工具';
  if (count === 1) return open ? `正在使用 ${name}` : `已使用 ${name}`;
  return open ? `正在使用 ${count} 个工具` : `已使用 ${count} 个工具`;
}

export function traceBlockLabel(block: DisplayBlock | undefined): string {
  if (!block) return '过程';
  if (block.kind === 'skill') return '已使用技能';
  if (block.kind === 'approval') return block.phase === 'open' ? '等待确认' : '已确认';
  if (block.kind === 'subagent') return '子任务';
  return '过程';
}

export function toolCallName(block: DisplayBlock | undefined): string {
  const name = record(block?.data).name;
  return typeof name === 'string' && name.trim() ? name.trim() : '工具';
}

export function toolDetail(block: DisplayBlock): { title: string; note: string } {
  const name = toolName(block) || '工具';
  const target = toolTarget(block.data);
  return { title: target || name, note: toolNote(block) };
}

/** Bash 展开后按 && 和分号折行，标题用命令里的分段名。 */
export function bashToolView(block: DisplayBlock): { summary: string; command: string; note: string } | null {
  if (blockToolFamily(block) !== 'bash') return null;
  const command = toolCommand(block);
  if (!command) return null;
  return {
    summary: shellSummary(command),
    command: formatShellCommand(command),
    note: toolNote(block),
  };
}

export function formatShellCommand(command: string): string {
  const lines: string[] = [];
  let current = '';
  let quote = '';
  const push = () => {
    const line = current.trim();
    current = '';
    if (line) lines.push(line);
  };
  for (let index = 0; index < command.length; index += 1) {
    const char = command[index];
    if (quote) {
      current += char;
      if (char === quote && command[index - 1] !== '\\') quote = '';
      continue;
    }
    if (char === '"' || char === '\'') {
      quote = char;
      current += char;
      continue;
    }
    if (char === '\n') {
      push();
      continue;
    }
    if (command.startsWith('&&', index)) {
      push();
      index += 1;
      continue;
    }
    if (char === ';') {
      push();
      continue;
    }
    current += char;
  }
  push();
  if (lines.length <= 40) return lines.join('\n');
  return `${lines.slice(0, 40).join('\n')}\n…`;
}

export function shellSummary(command: string): string {
  const titles = [...command.matchAll(/===\s*([^=]+?)\s*===/g)]
    .map((match) => match[1].trim())
    .filter(Boolean);
  const summary = titles.length
    ? titles.slice(0, 3).join('、')
    : (formatShellCommand(command).split('\n')[0] || '');
  if (summary.length <= 42) return summary;
  return `${summary.slice(0, 41)}…`;
}

function toolCommand(block: DisplayBlock): string {
  const root = record(block.data);
  const input = record(root.inputPreview) ?? record(root.input) ?? root;
  return stringField(input, 'command');
}

export function elapsedSeconds(start?: string, end?: string): number | null {
  if (!start || !end) return null;
  const ms = Date.parse(end) - Date.parse(start);
  if (!Number.isFinite(ms) || ms < 0) return null;
  return Math.max(1, Math.round(ms / 1000));
}

export function formatElapsed(seconds: number): string {
  const whole = Math.max(1, Math.round(seconds));
  if (whole < 60) return `${whole}秒`;
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  if (minutes < 60) return rest ? `${minutes}分${rest}秒` : `${minutes}分`;
  const hours = Math.floor(minutes / 60);
  const remain = minutes % 60;
  return remain ? `${hours}小时${remain}分` : `${hours}小时`;
}

/** 横线上面的字：运行中、用时或已中断。 */
export function messageStatusText(input: {
  running: boolean;
  stopped: boolean;
  seconds: number | null;
}): string {
  if (input.running) return '运行中';
  const duration = input.seconds != null ? formatElapsed(input.seconds) : '';
  if (input.stopped) return duration ? `已中断 · ${duration}` : '已中断';
  return duration ? `用时 ${duration}` : '已完成';
}

function activityFamily(snapshot: DisplaySnapshot, ids: string[]): string {
  return toolFamily(toolName(snapshot.blocksById[ids[0] || '']));
}

function isClosedApproval(block: DisplayBlock): boolean {
  return block.kind === 'approval' && block.phase !== 'open';
}

function textData(block: DisplayBlock): string {
  return typeof block.data === 'string' ? block.data : '';
}

function toolName(block: DisplayBlock | undefined): string {
  const name = record(block?.data).name;
  return typeof name === 'string' ? name : '';
}

export function toolFamily(name: string): string {
  const normalized = name.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (
    normalized.includes('webfetch')
    || normalized.includes('httprequest')
    || normalized.includes('websearch')
  ) return 'web';
  if (normalized === 'read') return 'read';
  if (normalized === 'grep' || normalized === 'glob') return 'search';
  if (normalized === 'bash') return 'bash';
  if (normalized === 'write' || normalized === 'edit') return 'edit';
  return `other:${normalized || 'tool'}`;
}

export function blockToolFamily(block: DisplayBlock | undefined): string {
  return toolFamily(toolName(block));
}

export type WebPageResult = {
  title: string;
  url: string;
  host: string;
};

/** 展开网页搜索时的查询词和结果行。查询词和标题都只取工具快照里已有的内容。 */
export function webSearchDetail(blocks: DisplayBlock[]): { query: string; pages: WebPageResult[] } {
  const queries = [...new Set(blocks.map(searchQuery).filter(Boolean))];
  return {
    query: queries.length === 1 ? queries[0] : '',
    pages: blocks.map(webPageResult),
  };
}

function toolTarget(data: unknown): string {
  const root = record(data);
  const input = record(root.inputPreview) ?? record(root.input) ?? root;
  const url = stringField(input, 'url') || stringField(root, 'url');
  if (url) return compactUrl(url);
  for (const key of ['path', 'file_path', 'file', 'pattern', 'command', 'query', 'glob']) {
    const value = stringField(input, key);
    if (value) return oneLine(value);
  }
  return '';
}

function toolNote(block: DisplayBlock): string {
  if (block.outcome !== 'failed' && block.phase !== 'open') return '';
  const data = record(block.data);
  const raw = stringField(data, 'error') || stringField(data, 'outputPreview');
  const line = raw.split('\n').map((item) => item.trim()).find(Boolean) || '';
  return line.slice(0, 160);
}

function oneLine(value: string): string {
  const line = value.split('\n')[0].trim();
  if (line.length <= 500) return line;
  return `${line.slice(0, 499)}…`;
}

function compactUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === '/' ? '' : parsed.pathname;
    return `${parsed.host}${path}`.slice(0, 96);
  } catch {
    return url.slice(0, 96);
  }
}

function record(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value !== 'string' || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value) as unknown;
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return {};
  }
  return {};
}

function stringField(data: Record<string, unknown>, key: string): string {
  const value = data[key];
  return typeof value === 'string' ? value.trim() : '';
}

const INPUT_QUERY_KEYS = ['query', 'q', 'keyword', 'wd', 'search_query', 'word'];
const URL_QUERY_KEYS = ['q', 'query', 'wd', 'keyword', 'word', 'search_query'];

function searchQuery(block: DisplayBlock): string {
  const data = record(block.data);
  const input = record(data.inputPreview);
  for (const key of INPUT_QUERY_KEYS) {
    const value = usableQuery(stringField(input, key));
    if (value) return value;
  }
  const rawInput = stringField(data, 'inputPreview');
  const url = stringField(input, 'url') || stringField(data, 'url') || looseHttpUrl(rawInput);
  return queryFromUrl(url);
}

function queryFromUrl(url: string): string {
  try {
    const parsed = new URL(url);
    for (const key of URL_QUERY_KEYS) {
      const value = usableQuery(parsed.searchParams.get(key) || '');
      if (value) return value;
    }
  } catch {
    return '';
  }
  return '';
}

function usableQuery(value: string): string {
  const cleaned = value.replace(/\s+/g, ' ').trim();
  if (!cleaned || /^https?:\/\//i.test(cleaned)) return '';
  return cleaned.slice(0, 160);
}

function webPageResult(block: DisplayBlock): WebPageResult {
  const outputText = stringField(record(block.data), 'outputPreview');
  const output = record(outputText);
  const url = pageUrl(block);
  const title = firstText(
    stringField(output, 'title'),
    jsonStringField(outputText, 'title'),
    htmlTitle(outputText),
    headingTitle(outputText),
    pageLabel(url),
    toolName(block),
    '网页',
  );
  return { title, url, host: pageHost(url) };
}

function pageUrl(block: DisplayBlock): string {
  const data = record(block.data);
  const input = record(data.inputPreview);
  const outputText = stringField(data, 'outputPreview');
  const output = record(outputText);
  const candidates = [
    stringField(output, 'final_url'),
    jsonStringField(outputText, 'final_url'),
    stringField(output, 'url'),
    jsonStringField(outputText, 'url'),
    stringField(input, 'url'),
    stringField(data, 'url'),
    looseHttpUrl(stringField(data, 'inputPreview')),
  ];
  for (const candidate of candidates) {
    const url = httpUrl(candidate);
    if (url) return url;
  }
  return '';
}

function httpUrl(value: string): string {
  if (!value) return '';
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
    return parsed.toString();
  } catch {
    return '';
  }
}

function looseHttpUrl(text: string): string {
  const match = /https?:\/\/[^\s"'<>\\]+/.exec(text);
  return match ? match[0] : '';
}

function pageHost(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return '';
  }
}

function pageLabel(url: string): string {
  if (!url) return '';
  try {
    const parsed = new URL(url);
    let path = parsed.pathname;
    try {
      path = decodeURIComponent(path);
    } catch {
      path = parsed.pathname;
    }
    if (path.length > 1) path = path.replace(/\/$/, '');
    if (path === '/') path = '';
    const host = parsed.hostname.replace(/^www\./, '');
    const label = `${host}${path}`;
    return label.length > 140 ? `${label.slice(0, 137)}...` : label;
  } catch {
    return '';
  }
}

function firstText(...values: string[]): string {
  for (const value of values) {
    const cleaned = value.replace(/\s+/g, ' ').trim();
    if (cleaned) return cleaned.slice(0, 180);
  }
  return '';
}

function jsonStringField(text: string, key: string): string {
  if (!text) return '';
  const match = new RegExp(`"${key}"\\s*:\\s*"((?:\\\\.|[^"\\\\])*)"`).exec(text);
  if (!match) return '';
  return unescapeJson(match[1]).replace(/\s+/g, ' ').trim();
}

function unescapeJson(value: string): string {
  try {
    const parsed = JSON.parse(`"${value}"`) as unknown;
    return typeof parsed === 'string' ? parsed : value;
  } catch {
    return value;
  }
}

function htmlTitle(text: string): string {
  const match = /<title[^>]*>([\s\S]{0,300}?)<\/title>/i.exec(text);
  if (!match) return '';
  return decodeEntities(match[1].replace(/<[^>]+>/g, ' '));
}

function headingTitle(text: string): string {
  const match = /^#{1,3}\s+(\S.*)$/m.exec(text);
  return match ? match[1].trim() : '';
}

function decodeEntities(value: string): string {
  return value
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'");
}
