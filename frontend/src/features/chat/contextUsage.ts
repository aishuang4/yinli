/** 从 DisplaySnapshot 读取当前会话上下文占用。 */

import type { DisplayBlock, DisplaySnapshot } from '../runtime-stream/protocol';
import type { LocalMessage } from './types';

/** 与 runtime-stream/selectors 的 blocksOfKind 等价；这里不引入 selectors，避免把 Block 渲染模块拉进纯数据层。 */
function blocksOfKind<T>(snapshot: DisplaySnapshot, kind: string): DisplayBlock<T>[] {
  return snapshot.order.flatMap((id) => {
    const block = snapshot.blocksById[id];
    return block?.kind === kind ? [block as DisplayBlock<T>] : [];
  });
}

export type ContextUsageData = {
  systemPromptChars?: number;
  summaryChars?: number;
  contextChars?: number;
  contextMessages?: number;
  toolResultBlocks?: number;
  toolCount?: number;
  contextWindow?: number;
  triggerRatio?: number;
  triggerTokens?: number;
  estimatedTokens?: number;
  compressed?: number;
  overTrigger?: number;
  inputTokens?: number;
  outputTokens?: number;
  /** 估算算法（与后端 AgentScope 压缩判定同源，目前为 bytes/4）。 */
  estimateMethod?: string;
  /** 估算相对供应商回报的偏差百分比；正值 = 估算偏高。 */
  estimateErrorPct?: number;
  /** system prompt 各段字符数，用于定位膨胀来源。 */
  promptSections?: Record<string, number>;
  toolSchemaChars?: number;
  dataBlocks?: number;
  /** AgentScope 真正压缩过的次数（本会话累计）。 */
  compressionCount?: number;
  /** 一级无模型整理累计：存根化的旧工具输出 / 移除的旧图片 / 省下的字符。 */
  stubbedToolResults?: number;
  droppedImages?: number;
  hygieneSavedChars?: number;
};

const PROMPT_SECTION_LABELS: Record<string, string> = {
  agent: 'Agent 提示词',
  project: '项目上下文',
  workspace: '工作目录',
  runtime: '运行时说明',
  skills: 'Skill 说明',
  route: '路由指令',
  skillRun: 'Skill 运行态',
  memory: '记忆注入',
  clarification: '澄清契约',
  finalAnswer: '最终回答契约',
};

/** 按字符数倒序列出 system prompt 各段，供 tooltip 展示。 */
export function describePromptSections(usage: ContextUsageData, limit = 4): string[] {
  const sections = usage.promptSections;
  if (!sections || typeof sections !== 'object') {
    return [];
  }
  return Object.entries(sections)
    .filter(([, chars]) => Number(chars) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, limit)
    .map(([key, chars]) => `${PROMPT_SECTION_LABELS[key] ?? key} ${Number(chars)} 字`);
}

export function selectContextUsage(snapshot?: DisplaySnapshot | null): ContextUsageData | null {
  if (!snapshot) {
    return null;
  }
  const block = blocksOfKind<ContextUsageData>(snapshot, 'context').at(-1);
  if (!block || !block.data || typeof block.data !== 'object') {
    return null;
  }
  return block.data;
}

export function selectLatestContextUsage(
  live: DisplaySnapshot,
  messages: LocalMessage[],
): ContextUsageData | null {
  return selectContextUsage(live)
    || [...messages].reverse().map((item) => selectContextUsage(item.displaySnapshot)).find(Boolean)
    || null;
}

export function resolveContextUsedTokens(usage: ContextUsageData): { used: number; estimated: boolean } {
  const actual = Number(usage.inputTokens || 0);
  if (actual > 0) {
    return { used: actual, estimated: false };
  }
  return { used: Number(usage.estimatedTokens || 0), estimated: true };
}
