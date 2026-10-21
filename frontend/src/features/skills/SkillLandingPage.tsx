/**
 * SkillLandingPage — Skills 第一层列表入口。
 *
 * 负责筛选条、列表行和对象级快捷操作；具体 Skill 内容放到二级详情页展示。
 */

import { Download, List, Package, Plus, RefreshCw, Trash2, Upload } from 'lucide-react';
import { Button, Tag } from '@/components/design';
import { FileImportButton } from '../../components/FileImportButton';
import { CatalogFilterBar, type CatalogInstallFilter } from '../../components/form/CatalogFilterBar';
import {
  WorkbenchEmptyPanel,
  WorkbenchList,
  WorkbenchListIcon,
  WorkbenchListPage,
  WorkbenchListRow,
  WorkbenchLoadingPanel,
  WorkbenchSection,
} from '../../components/workbench';
import { ToggleButton } from '../../components/ToggleButton';
import type { Skill } from '../../types/skill';
import { isSkillShared } from '../../types/skill';

interface SkillLandingPageProps {
  skills: Skill[];
  totalCount: number;
  installedCount: number;
  enabledCount: number;
  categories: string[];
  loading: boolean;
  error: string | null;
  importError: string | null;
  importing: boolean;
  busyId: string | null;
  query: string;
  category: string;
  installFilter: CatalogInstallFilter;
  onQueryChange: (value: string) => void;
  onCategoryChange: (value: string) => void;
  onInstallFilterChange: (value: CatalogInstallFilter) => void;
  onRefresh: () => void;
  onRegisterOpen: () => void;
  onImportArchive: (file: File | null | undefined) => void;
  onSelectSkill: (skill: Skill) => void;
  onExportSkill: (skill: Skill) => void;
  onInstallSkill: (skill: Skill) => void;
  onUninstallSkill: (skill: Skill) => void;
  onDeleteSkill: (skill: Skill) => void;
  onToggleSkill: (skill: Skill, enabled: boolean) => void;
}

export function SkillLandingPage({
  skills,
  totalCount,
  installedCount,
  enabledCount,
  categories,
  loading,
  error,
  importError,
  importing,
  busyId,
  query,
  category,
  installFilter,
  onQueryChange,
  onCategoryChange,
  onInstallFilterChange,
  onRefresh,
  onRegisterOpen,
  onImportArchive,
  onSelectSkill,
  onExportSkill,
  onInstallSkill,
  onUninstallSkill,
  onDeleteSkill,
  onToggleSkill,
}: SkillLandingPageProps) {
  return (
    <WorkbenchListPage
      title="Skills"
      icon={<Package size={18} />}
      actions={(
        <>
          <Tag size="small" color="blue">{totalCount} 个 Skill</Tag>
          <Tag size="small" color="green">{installedCount} 个已安装</Tag>
          <Tag size="small" color="transparent">{enabledCount} 个已启用</Tag>
          <Button type="primary" icon={<Plus size={15} />} onClick={onRegisterOpen}>
            注册
          </Button>
          <FileImportButton
            accept=".zip,application/zip"
            loading={importing}
            icon={<Upload size={14} />}
            onFile={onImportArchive}
          >
            导入
          </FileImportButton>
          <Button icon={<RefreshCw size={14} />} onClick={onRefresh} loading={loading}>
            刷新
          </Button>
        </>
      )}
    >
      <div className="mb-5">
        <CatalogFilterBar
          query={query}
          category={category}
          installFilter={installFilter}
          categories={categories}
          searchPlaceholder="搜索 Skill、触发条件或标签"
          onQueryChange={onQueryChange}
          onCategoryChange={onCategoryChange}
          onInstallFilterChange={onInstallFilterChange}
        />
      </div>

      {importError ? (
        <div className="mb-5">
          <WorkbenchEmptyPanel>
            <span className="text-[var(--color-error)]">{importError}</span>
          </WorkbenchEmptyPanel>
        </div>
      ) : null}

      <WorkbenchSection title="Skill 定义" compact>
        {error ? (
          <WorkbenchEmptyPanel>
            <span className="text-[var(--color-error)]">{error}</span>
          </WorkbenchEmptyPanel>
        ) : loading ? (
          <WorkbenchLoadingPanel tip="加载 Skills..." />
        ) : skills.length === 0 ? (
          <WorkbenchEmptyPanel>没有匹配的 Skill</WorkbenchEmptyPanel>
        ) : (
          <WorkbenchList>
            {skills.map((skill, index) => (
              <SkillListRow
                key={skill.id}
                skill={skill}
                busy={busyId === skill.id}
                last={index === skills.length - 1}
                onSelect={() => onSelectSkill(skill)}
                onExport={() => onExportSkill(skill)}
                onInstall={() => onInstallSkill(skill)}
                onUninstall={() => onUninstallSkill(skill)}
                onDelete={() => onDeleteSkill(skill)}
                onToggle={(enabled) => onToggleSkill(skill, enabled)}
              />
            ))}
          </WorkbenchList>
        )}
      </WorkbenchSection>
    </WorkbenchListPage>
  );
}

function SkillListRow({
  skill,
  busy,
  last,
  onSelect,
  onExport,
  onInstall,
  onUninstall,
  onDelete,
  onToggle,
}: {
  skill: Skill;
  busy: boolean;
  last: boolean;
  onSelect: () => void;
  onExport: () => void;
  onInstall: () => void;
  onUninstall: () => void;
  onDelete: () => void;
  onToggle: (enabled: boolean) => void;
}) {
  const installed = skill.status === 'installed';

  return (
    <WorkbenchListRow
      icon={(
        <WorkbenchListIcon>
          <Package size={20} strokeWidth={1.8} />
        </WorkbenchListIcon>
      )}
      title={skill.name}
      subtitle={skill.trigger || skill.id}
      last={last}
      layout="columns"
      onSelect={onSelect}
      meta={(
        <span className="grid w-full grid-cols-[5rem_5rem_8rem_7rem] items-center gap-2">
          <span className="justify-self-start">
            <Tag size="small" color={installed ? 'green' : 'blue'}>{installed ? '已安装' : '可安装'}</Tag>
          </span>
          <span className="min-w-0 justify-self-start">
            <Tag size="small" color="transparent">{skill.category}</Tag>
          </span>
          <span className="justify-self-start">
            {isSkillShared(skill) ? <Tag size="small" color="blue">所有 Agent 可用</Tag> : <Tag size="small" color="transparent">需绑定</Tag>}
          </span>
          <span className="justify-self-start">
            {(skill.warnings?.length ?? 0) > 0 ? <Tag size="small" color="orange">平台兼容提示</Tag> : null}
          </span>
        </span>
      )}
      trailing={(
        <span className="grid grid-cols-[6rem_4rem_2rem_2rem_2rem] items-center gap-2">
          <span className="justify-self-center">
            {installed ? (
              <ToggleButton
                checked={skill.enabled}
                checkedLabel="已启用"
                uncheckedLabel="已停用"
                disabled={busy}
                ariaLabel={`${skill.name} 启停状态`}
                onChange={onToggle}
              />
            ) : null}
          </span>
          <span className="justify-self-center">
            {installed ? (
              <Button size="small" danger loading={busy} onClick={onUninstall}>
                卸载
              </Button>
            ) : (
              <Button size="small" type="primary" loading={busy} onClick={onInstall}>
                安装
              </Button>
            )}
          </span>
          <button
            type="button"
            onClick={onExport}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            aria-label={`导出 ${skill.name}`}
          >
            <Download size={15} />
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-error-bg)] hover:text-[var(--color-error)]"
            aria-label={`删除 ${skill.name}`}
          >
            <Trash2 size={15} />
          </button>
          <button
            type="button"
            onClick={onSelect}
            className="icon-button h-8 w-8 rounded-md text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            aria-label={`打开 ${skill.name} 详情`}
          >
            <List size={17} />
          </button>
        </span>
      )}
    />
  );
}
