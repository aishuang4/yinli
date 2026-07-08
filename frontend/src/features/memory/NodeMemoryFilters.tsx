/**
 * NodeMemoryFilters — 历史记忆检索筛选条。
 *
 * 统一历史记忆的关键词、日期范围和查询按钮布局。
 */

import { Search } from 'lucide-react';
import { Button } from '@agentscope-ai/design';
import { DateField, SelectField, TextField } from '../../components/form';
import type { MemoryNodeStatus } from '../../types/memory';
import { NODE_STATUS_OPTIONS } from './memoryViewConfig';

interface NodeMemoryFiltersProps {
  query: string;
  setQuery: (value: string) => void;
  dateFrom: string;
  setDateFrom: (value: string) => void;
  dateTo: string;
  setDateTo: (value: string) => void;
  status: MemoryNodeStatus | 'all';
  setStatus: (value: MemoryNodeStatus | 'all') => void;
  loading: boolean;
  onSearch: () => void;
}

export function NodeMemoryFilters({
  query,
  setQuery,
  dateFrom,
  setDateFrom,
  dateTo,
  setDateTo,
  status,
  setStatus,
  loading,
  onSearch,
}: NodeMemoryFiltersProps) {
  return (
    <div className="grid grid-cols-[minmax(260px,1fr)_150px_150px_150px_auto] items-end gap-3">
      <TextField
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索历史摘要、用户输入或 Agent 输出"
        prefix={<Search size={14} />}
        allowClear
      />
      <DateField
        value={dateFrom}
        onChange={setDateFrom}
        placeholder="开始日期"
      />
      <DateField
        value={dateTo}
        onChange={setDateTo}
        placeholder="结束日期"
      />
      <SelectField
        label=""
        value={status}
        options={NODE_STATUS_OPTIONS}
        onChange={(value) => setStatus(value as MemoryNodeStatus | 'all')}
      />
      <Button type="primary" icon={<Search size={14} />} onClick={onSearch} loading={loading}>
        查询
      </Button>
    </div>
  );
}
