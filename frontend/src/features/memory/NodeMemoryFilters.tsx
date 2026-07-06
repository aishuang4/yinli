/**
 * NodeMemoryFilters — 历史记忆检索筛选条。
 *
 * 统一历史记忆的关键词、日期范围和查询按钮布局。
 */

import { Search } from 'lucide-react';
import { Button } from '@agentscope-ai/design';
import { DateField, TextField } from '../../components/form';

interface NodeMemoryFiltersProps {
  query: string;
  setQuery: (value: string) => void;
  dateFrom: string;
  setDateFrom: (value: string) => void;
  dateTo: string;
  setDateTo: (value: string) => void;
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
  loading,
  onSearch,
}: NodeMemoryFiltersProps) {
  return (
    <div className="grid grid-cols-[minmax(260px,1fr)_150px_150px_auto] items-end gap-3">
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
      <Button type="primary" icon={<Search size={14} />} onClick={onSearch} loading={loading}>
        查询
      </Button>
    </div>
  );
}
