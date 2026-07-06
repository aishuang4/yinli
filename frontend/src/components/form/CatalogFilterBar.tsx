/**
 * CatalogFilterBar — MCP / Skill 等目录页的统一筛选栏。
 */

import { Search } from 'lucide-react';
import { SelectField, TextField } from '.';

export type CatalogInstallFilter = 'all' | 'installed' | 'available';

const installFilterOptions = [
  { label: '全部', value: 'all' },
  { label: '已安装', value: 'installed' },
  { label: '可安装', value: 'available' },
];

type CatalogFilterBarProps = {
  query: string;
  category: string;
  installFilter: CatalogInstallFilter;
  categories: string[];
  searchPlaceholder: string;
  onQueryChange: (value: string) => void;
  onCategoryChange: (value: string) => void;
  onInstallFilterChange: (value: CatalogInstallFilter) => void;
};

export function CatalogFilterBar({
  query,
  category,
  installFilter,
  categories,
  searchPlaceholder,
  onQueryChange,
  onCategoryChange,
  onInstallFilterChange,
}: CatalogFilterBarProps) {
  const categoryOptions = [
    { label: '全部分类', value: 'all' },
    ...categories.map((item) => ({ label: item, value: item })),
  ];

  return (
    <div className="grid grid-cols-[minmax(240px,1fr)_180px_160px] gap-3">
      <TextField
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        placeholder={searchPlaceholder}
        prefix={<Search size={14} />}
        allowClear
      />
      <SelectField
        value={category}
        onChange={(value) => onCategoryChange(String(value))}
        options={categoryOptions}
      />
      <SelectField
        value={installFilter}
        onChange={(value) => onInstallFilterChange(value as CatalogInstallFilter)}
        options={installFilterOptions}
      />
    </div>
  );
}
