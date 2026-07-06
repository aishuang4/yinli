/**
 * HttpToolFilters — HTTP 工具列表的搜索与刷新控件。
 */

import { RefreshCw } from 'lucide-react';
import { Button } from '@agentscope-ai/design';
import { TextField } from '../../components/form';

type HttpToolFiltersProps = {
  query: string;
  loading: boolean;
  onQueryChange: (value: string) => void;
  onRefresh: () => void;
};

export function HttpToolFilters({
  query,
  loading,
  onQueryChange,
  onRefresh,
}: HttpToolFiltersProps) {
  return (
    <div className="grid flex-1 grid-cols-[minmax(240px,1fr)_auto] gap-3">
      <TextField
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        placeholder="搜索 HTTP 工具、URL 或说明"
        allowClear
      />
      <Button icon={<RefreshCw size={14} />} onClick={onRefresh} loading={loading}>
        刷新
      </Button>
    </div>
  );
}
