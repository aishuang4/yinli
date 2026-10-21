/**
 * memoryViewConfig — 记忆页面视图配置与展示辅助函数。
 */

import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  Database,
  FileText,
  Globe2,
  Settings2,
  UserRound,
  XCircle,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type {
  MarkdownMemoryScope,
  MemoryCandidateStatus,
  MemoryMarkdownCandidate,
  MemoryNodeStatus,
} from '../../types/memory';

export type MemoryTab = 'habits' | 'professional' | 'timeline' | 'markdown' | 'candidates' | 'nodes' | 'settings';

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
  { key: 'habits', label: '习惯', icon: UserRound },
  { key: 'professional', label: '专业区', icon: Bot },
  { key: 'timeline', label: '时间线', icon: Clock3 },
  { key: 'markdown', label: '旧 Markdown', icon: FileText },
  { key: 'candidates', label: '待确认记忆', icon: ClipboardCheck },
  { key: 'nodes', label: '旧历史节点', icon: Database },
  { key: 'settings', label: '设置与审计', icon: Settings2 },
];

export const NODE_STATUS_OPTIONS: Array<{ label: string; value: MemoryNodeStatus | 'all' }> = [
  { label: '活跃记忆', value: 'active' },
  { label: '全部状态', value: 'all' },
  { label: '已禁用', value: 'disabled' },
  { label: '已归档', value: 'archived' },
  { label: '已删除', value: 'deleted' },
];

export const CANDIDATE_STATUS_OPTIONS: Array<{ label: string; value: MemoryCandidateStatus | 'all' }> = [
  { label: '待确认', value: 'pending' },
  { label: '全部状态', value: 'all' },
  { label: '已采纳', value: 'accepted' },
  { label: '已拒绝', value: 'rejected' },
  { label: '采纳失败', value: 'failed' },
];

export function candidateScopeLabel(scope: MemoryMarkdownCandidate['scope']): string {
  if (scope === 'user') return '用户记忆';
  if (scope === 'global_preference') return '全局记忆';
  return 'Agent 记忆';
}

export function candidateStatusMeta(status: MemoryCandidateStatus): {
  label: string;
  color: 'default' | 'success' | 'blue' | 'mauve';
  icon: LucideIcon;
} {
  if (status === 'accepted') {
    return { label: '已采纳', color: 'success', icon: CheckCircle2 };
  }
  if (status === 'rejected') {
    return { label: '已拒绝', color: 'default', icon: XCircle };
  }
  if (status === 'failed') {
    return { label: '采纳失败', color: 'mauve', icon: AlertTriangle };
  }
  return { label: '待确认', color: 'blue', icon: Clock3 };
}

export function formatDateTime(value: string): string {
  if (!value) return '未知时间';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}
