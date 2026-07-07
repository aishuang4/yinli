/**
 * memoryViewConfig — 记忆页面视图配置与展示辅助函数。
 */

import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Database,
  FileText,
  FolderSync,
  Globe2,
  Sparkles,
  UserRound,
  XCircle,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { MarkdownMemoryScope, SkillEvolutionRecordStatus } from '../../types/memory';

export type MemoryTab = 'markdown' | 'nodes' | 'skills' | 'wiki';

export interface MarkdownTarget {
  key: MarkdownMemoryScope;
  label: string;
  description: string;
  icon: typeof UserRound;
}

export const MARKDOWN_TARGETS: MarkdownTarget[] = [
  { key: 'user', label: '用户记忆', description: '唯一用户长期记忆', icon: UserRound },
  { key: 'agent', label: 'Agent 记忆', description: '按 Agent 独立保存', icon: Bot },
  { key: 'global', label: '全局记忆', description: '唯一全局共享规则', icon: Globe2 },
];

export const MEMORY_TABS: Array<{ key: MemoryTab; label: string; icon: typeof FileText }> = [
  { key: 'markdown', label: 'Markdown 记忆', icon: FileText },
  { key: 'nodes', label: '历史记忆', icon: Database },
  { key: 'skills', label: 'Skill 自进化队列', icon: Sparkles },
  { key: 'wiki', label: 'Wiki Vault', icon: FolderSync },
];

export const SKILL_STATUS_OPTIONS: Array<{ label: string; value: SkillEvolutionRecordStatus | 'all' }> = [
  { label: '全部状态', value: 'all' },
  { label: '草稿候选', value: 'draft' },
  { label: '已注册', value: 'registered' },
  { label: '已跳过', value: 'skipped' },
  { label: '失败', value: 'failed' },
];

export function skillStatusMeta(status: SkillEvolutionRecordStatus): {
  label: string;
  color: 'default' | 'success' | 'blue' | 'mauve';
  icon: LucideIcon;
} {
  if (status === 'registered') {
    return { label: '已注册', color: 'success', icon: CheckCircle2 };
  }
  if (status === 'draft') {
    return { label: '草稿', color: 'blue', icon: Sparkles };
  }
  if (status === 'failed') {
    return { label: '失败', color: 'mauve', icon: XCircle };
  }
  return { label: '已跳过', color: 'default', icon: AlertTriangle };
}

export function formatDateTime(value: string): string {
  if (!value) return '未知时间';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}
