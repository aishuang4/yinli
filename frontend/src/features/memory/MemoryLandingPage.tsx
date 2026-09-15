/**
 * MemoryLandingPage — Memory 模块第一层入口。
 *
 * 将 Markdown 记忆、历史记忆、Skill 自进化、Wiki 和设置审计收束为统一入口列表。
 */

import { Brain, List, RefreshCw } from 'lucide-react';
import { Button, Tag } from '@/components/design';
import {
  WorkbenchList,
  WorkbenchListIcon,
  WorkbenchListPage,
  WorkbenchListRow,
  WorkbenchSection,
} from '../../components/workbench';
import { MEMORY_TABS, type MemoryTab } from './memoryViewConfig';

interface MemoryLandingPageProps {
  pendingCandidateCount?: number;
  onSelectTab: (tab: MemoryTab) => void;
  onRefresh: () => void;
}

export function MemoryLandingPage({
  pendingCandidateCount = 0,
  onSelectTab,
  onRefresh,
}: MemoryLandingPageProps) {
  return (
    <WorkbenchListPage
      title="Memory"
      icon={<Brain size={18} />}
      actions={(
        <>
          <Tag size="small" color="blue">{MEMORY_TABS.length} 个模块</Tag>
          <Button icon={<RefreshCw size={14} />} onClick={onRefresh}>
            刷新
          </Button>
        </>
      )}
    >
      <WorkbenchSection title="记忆管理" compact>
        <WorkbenchList>
          {MEMORY_TABS.map((item, index) => {
            const Icon = item.icon;
            return (
              <WorkbenchListRow
                key={item.key}
                icon={(
                  <WorkbenchListIcon>
                    <Icon size={20} strokeWidth={1.8} />
                  </WorkbenchListIcon>
                )}
                title={item.key === 'candidates' && pendingCandidateCount > 0
                  ? `${item.label}（${pendingCandidateCount} 条待处理）`
                  : item.label}
                subtitle={memoryTabDescription(item.key)}
                last={index === MEMORY_TABS.length - 1}
                onSelect={() => onSelectTab(item.key)}
                trailing={(
                  <button
                    type="button"
                    onClick={() => onSelectTab(item.key)}
                    className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                    aria-label={`打开 ${item.label}`}
                  >
                    <List size={17} />
                  </button>
                )}
              />
            );
          })}
        </WorkbenchList>
      </WorkbenchSection>
    </WorkbenchListPage>
  );
}

function memoryTabDescription(tab: MemoryTab): string {
  if (tab === 'markdown') return '管理用户、Agent 和全局 Markdown 长期记忆';
  if (tab === 'candidates') return '采纳或拒绝模型提炼出的用户 / 全局记忆，采纳后才写入文件';
  if (tab === 'nodes') return '检索、禁用、归档或恢复历史记忆节点';
  if (tab === 'skills') return '查看和处理 Skill 自进化候选队列';
  if (tab === 'wiki') return '同步只读 Wiki 视图并查看实体关系';
  return '调整记忆准入策略并查看 MEMORY_AUDIT 审计';
}
