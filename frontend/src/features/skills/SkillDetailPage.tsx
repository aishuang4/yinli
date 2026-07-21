/**
 * SkillDetailPage — 单个 Skill 的二级详情页。
 *
 * 用统一详情外壳展示 Skill 元数据、触发条件、提示内容和资源文件。
 */

import { AlertTriangle, Download, Package, Trash2 } from 'lucide-react';
import { Button, Tag } from '@agentscope-ai/design';
import { MarkdownContent } from '../../components/MarkdownContent';
import { WorkbenchDetailShell, WorkbenchListIcon } from '../../components/workbench';
import { ToggleButton } from '../../components/ToggleButton';
import type { Skill } from '../../types/skill';

interface SkillDetailPageProps {
  skill: Skill;
  busy: boolean;
  onBack: () => void;
  onExport: () => void;
  onInstall: () => void;
  onUninstall: () => void;
  onDelete: () => void;
  onToggle: (enabled: boolean) => void;
}

export function SkillDetailPage({
  skill,
  busy,
  onBack,
  onExport,
  onInstall,
  onUninstall,
  onDelete,
  onToggle,
}: SkillDetailPageProps) {
  const installed = skill.status === 'installed';
  const statusText = installed ? (skill.enabled ? '已启用' : '已停用') : '未安装';

  return (
    <WorkbenchDetailShell
      title={skill.name}
      subtitle={skill.description || '暂无描述'}
      onBack={onBack}
      actions={(
        <>
          {installed ? (
            <ToggleButton
              checked={skill.enabled}
              checkedLabel="启用中"
              uncheckedLabel="已停用"
              disabled={busy}
              ariaLabel={`${skill.name} 启停状态`}
              onChange={onToggle}
            />
          ) : null}
          <Button size="small" icon={<Download size={13} />} loading={busy} onClick={onExport}>
            导出
          </Button>
          {installed ? (
            <Button size="small" danger loading={busy} onClick={onUninstall}>
              卸载
            </Button>
          ) : (
            <Button size="small" type="primary" loading={busy} onClick={onInstall}>
              安装
            </Button>
          )}
          <Button size="small" danger icon={<Trash2 size={13} />} onClick={onDelete}>
            删除
          </Button>
        </>
      )}
    >
      <div className="h-full min-h-0 overflow-y-auto p-5">
        <div className="mx-auto grid w-full max-w-5xl gap-4">
          <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
            <div className="mb-4 flex items-center gap-3">
            <WorkbenchListIcon>
              <Package size={20} strokeWidth={1.8} />
            </WorkbenchListIcon>
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{skill.name}</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  <Tag size="small" color={installed ? 'green' : 'blue'}>{installed ? '已安装' : '可安装'}</Tag>
                  <Tag size="small" color="transparent">{statusText}</Tag>
                  <Tag size="small" color="transparent">{skill.category}</Tag>
                </div>
              </div>
            </div>
            <div className="grid gap-3 text-sm md:grid-cols-2">
              <DetailField label="分类" value={skill.category} />
              <DetailField label="状态" value={statusText} />
              <DetailField label="更新时间" value={formatDateTime(skill.updatedAt)} />
              <DetailField label="安装时间" value={formatDateTime(skill.installedAt)} />
            </div>
          </section>

          {skill.tags.length > 0 ? (
            <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
              <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">标签</div>
              <div className="flex flex-wrap gap-2">
                {skill.tags.map((tag) => (
                  <Tag key={tag} size="small" color="blue">{tag}</Tag>
                ))}
              </div>
            </section>
          ) : null}

          {(skill.warnings?.length ?? 0) > 0 ? (
            <section className="rounded-lg border border-[var(--color-warning)] bg-[var(--color-bg-primary)] p-4">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
                <AlertTriangle size={15} color="var(--color-warning)" />
                兼容性提示
              </div>
              <div className="space-y-2 text-sm text-[var(--color-text-secondary)]">
                {(skill.warnings ?? []).map((warning) => (
                  <div key={warning}>{warning}</div>
                ))}
              </div>
            </section>
          ) : null}

          <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
            <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">触发条件</div>
            <div className="break-words rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-sm leading-6 text-[var(--color-text-primary)]">
              {skill.trigger || '未配置'}
            </div>
          </section>

          <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
            <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">内容</div>
            <div className="max-h-[420px] overflow-auto rounded-md bg-[var(--color-bg-secondary)] px-4 py-3 text-sm text-[var(--color-text-primary)]">
              <MarkdownContent content={skill.content || '未配置'} />
            </div>
          </section>

          {skill.resources.length > 0 ? (
            <section className="rounded-xl bg-[var(--color-bg-primary)] p-4">
              <div className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">资源文件</div>
              <div className="rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
                <div className="mb-2 text-[var(--color-text-tertiary)]">{skill.resourcePath}</div>
                <div className="max-h-[220px] space-y-1 overflow-auto">
                  {skill.resources.map((resource) => (
                    <div key={resource} className="break-all">{resource}</div>
                  ))}
                </div>
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </WorkbenchDetailShell>
  );
}

function DetailField({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-[var(--color-bg-secondary)] p-3">
      <div className="text-xs text-[var(--color-text-tertiary)]">{label}</div>
      <div className="mt-1 break-words text-sm font-medium text-[var(--color-text-primary)]">{value}</div>
    </div>
  );
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '未安装';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN');
}
