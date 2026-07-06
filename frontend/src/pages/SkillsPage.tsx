/**
 * SkillsPage — Skills 浏览器页面
 *
 * 支持浏览 Skills、按分类/安装状态筛选、注册自定义 Skill、安装/卸载、启停和查看详情。
 */

import { useMemo, useState } from 'react';
import { CheckCircle2, Download, Package, Plus, RefreshCw, Trash2, Upload } from 'lucide-react';
import {
  Button,
  Card,
  Empty,
  Modal,
  Spinner,
  Tag,
} from '@agentscope-ai/design';
import { SkillRegisterModal, type SkillRegisterFormState } from '../features/skills/SkillRegisterModal';
import { ToggleButton } from '../components/ToggleButton';
import { FileImportButton } from '../components/FileImportButton';
import { CatalogFilterBar, type CatalogInstallFilter } from '../components/form/CatalogFilterBar';
import { useSkills } from '../hooks/useSkills';
import type { Skill } from '../types/skill';

function skillMatchesQuery(skill: Skill, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;

  return [
    skill.name,
    skill.description,
    skill.category,
    skill.trigger,
    ...skill.tags,
  ].some((value) => value.toLowerCase().includes(normalized));
}

function parseTags(value: string): string[] {
  return value
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 8);
}

function skillArchiveFilename(name: string): string {
  return `${name.trim().replace(/[\\/:*?"<>|\r\n]+/g, '-').replace(/^[ .-]+|[ .-]+$/g, '') || 'skill'}.zip`;
}

export function SkillsPage() {
  const {
    skills,
    categories,
    loading,
    error,
    refresh,
    registerSkill,
    importSkillArchive,
    exportSkill,
    installSkill,
    uninstallSkill,
    deleteSkill,
    setSkillEnabled,
  } = useSkills();

  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [installFilter, setInstallFilter] = useState<CatalogInstallFilter>('all');
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<Skill | null>(null);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [importing, setImporting] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [registerForm, setRegisterForm] = useState<SkillRegisterFormState>({
    name: '',
    description: '',
    category: '自定义',
    trigger: '',
    content: '',
    tags: '',
  });

  const filteredSkills = useMemo(() => (
    skills.filter((skill) => {
      if (!skillMatchesQuery(skill, query)) return false;
      if (category !== 'all' && skill.category !== category) return false;
      if (installFilter === 'installed') return skill.status === 'installed';
      if (installFilter === 'available') return skill.status !== 'installed';
      return true;
    })
  ), [skills, query, category, installFilter]);

  const installedCount = skills.filter((skill) => skill.status === 'installed').length;
  const enabledCount = skills.filter((skill) => skill.status === 'installed' && skill.enabled).length;

  async function runSkillAction(
    skillId: string,
    action: () => Promise<Skill>,
  ) {
    try {
      setBusyId(skillId);
      const next = await action();
      setSelectedSkill((current) => (current?.id === next.id ? next : current));
    } finally {
      setBusyId(null);
    }
  }

  async function handleRegister() {
    if (!registerForm.name.trim()) {
      setRegisterError('请输入 Skill 名称');
      return;
    }

    try {
      setRegistering(true);
      setRegisterError(null);
      const skill = await registerSkill({
        name: registerForm.name.trim(),
        description: registerForm.description.trim() || undefined,
        category: registerForm.category.trim() || '自定义',
        trigger: registerForm.trigger.trim() || undefined,
        content: registerForm.content.trim() || undefined,
        tags: parseTags(registerForm.tags),
      });
      setRegisterOpen(false);
      setRegisterForm({
        name: '',
        description: '',
        category: '自定义',
        trigger: '',
        content: '',
        tags: '',
      });
      setSelectedSkill(skill);
    } catch (err) {
      setRegisterError(err instanceof Error ? err.message : '注册 Skill 失败');
    } finally {
      setRegistering(false);
    }
  }

  async function handleImportArchive(file: File | null | undefined) {
    if (!file) return;

    try {
      setImporting(true);
      setImportError(null);
      const skill = await importSkillArchive(file);
      setSelectedSkill(skill);
    } catch (err) {
      setImportError(err instanceof Error ? err.message : '导入 Skill 压缩包失败');
    } finally {
      setImporting(false);
    }
  }

  async function handleDeleteSkill() {
    if (!deleteConfirm) return;
    try {
      setBusyId(deleteConfirm.id);
      await deleteSkill(deleteConfirm.id);
      setSelectedSkill((current) => (current?.id === deleteConfirm.id ? null : current));
      setDeleteConfirm(null);
    } finally {
      setBusyId(null);
    }
  }

  async function handleExportSkill(skill: Skill) {
    try {
      setBusyId(skill.id);
      await exportSkill(skill.id, skillArchiveFilename(skill.name));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="h-full grid grid-rows-[auto_auto_1fr] min-h-0 bg-[var(--color-bg-primary)]">
      <header className="px-5 py-4 border-b border-[var(--color-border)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Package size={20} color="var(--color-accent)" />
              <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">
                Skills Browser
              </h1>
            </div>
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              浏览、安装和管理 Agent 可加载的 Skills。
            </p>
          </div>
          <div className="flex items-center gap-3 text-sm text-[var(--color-text-secondary)]">
            <span>{skills.length} 个 Skill</span>
            <span>{installedCount} 个已安装</span>
            <span>{enabledCount} 个已启用</span>
            <Button
              type="primary"
              onClick={() => {
                setRegisterError(null);
                setRegisterOpen(true);
              }}
            >
              <span className="inline-flex items-center gap-1.5">
                <Plus size={14} className="shrink-0" />
                注册 Skill
              </span>
            </Button>
            <FileImportButton
              accept=".zip,application/zip"
              loading={importing}
              icon={<Upload size={14} className="shrink-0" />}
              onFile={(file) => {
                setImportError(null);
                void handleImportArchive(file);
              }}
            >
              导入压缩包
            </FileImportButton>
            <Button
              icon={<RefreshCw size={14} />}
              onClick={refresh}
              loading={loading}
            >
              刷新
            </Button>
          </div>
        </div>
      </header>

      <section className="px-5 py-3 border-b border-[var(--color-border)]">
        <CatalogFilterBar
          query={query}
          category={category}
          installFilter={installFilter}
          categories={categories}
          searchPlaceholder="搜索 Skill、触发条件或标签"
          onQueryChange={setQuery}
          onCategoryChange={setCategory}
          onInstallFilterChange={setInstallFilter}
        />
      </section>

      <main className="min-h-0 overflow-y-auto p-5">
        {importError && (
          <div className="mb-4 rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] p-4 text-sm text-[var(--color-error)]">
            {importError}
          </div>
        )}
        {error ? (
          <div className="rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] p-4 text-sm text-[var(--color-error)]">
            {error}
          </div>
        ) : loading ? (
          <div className="flex justify-center py-12">
            <Spinner tip="加载 Skills..." />
          </div>
        ) : filteredSkills.length === 0 ? (
          <Empty type="noData" description="没有匹配的 Skill" />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {filteredSkills.map((skill) => {
              const installed = skill.status === 'installed';
              const busy = busyId === skill.id;

              return (
                <Card
                  key={skill.id}
                  className="h-[400px] overflow-hidden transition-all duration-300 ease-out hover:-translate-y-1 hover:shadow-lg"
                  bodyStyle={{ height: '100%', padding: 0 }}
                >
                  <div className="grid h-full grid-rows-[minmax(0,62px)_minmax(0,58px)_minmax(0,32px)_minmax(0,66px)_minmax(0,34px)_minmax(0,1fr)_minmax(0,62px)] overflow-hidden">
                    <div className="flex min-h-0 items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3">
                      <h2 className="line-clamp-2 min-w-0 text-sm font-semibold text-[var(--color-text-primary)]">
                        {skill.name}
                      </h2>
                      <div className="shrink-0">
                        <Tag color={installed ? 'success' : 'info'} size="small">
                          {installed ? '已安装' : '可安装'}
                        </Tag>
                      </div>
                    </div>

                    <div className="min-h-0 overflow-hidden px-4 py-2">
                      <p className="line-clamp-2 text-xs text-[var(--color-text-secondary)]">
                        {skill.description}
                      </p>
                    </div>

                    <div className="min-h-0 overflow-hidden px-4">
                      <div className="flex h-7 flex-nowrap gap-2 overflow-hidden">
                        <Tag size="small" color="blue">{skill.category}</Tag>
                        {skill.tags.slice(0, 3).map((tag) => (
                          <Tag key={tag} size="small" color="mauve">{tag}</Tag>
                        ))}
                      </div>
                    </div>

                    <div className="grid min-h-0 grid-cols-2 gap-2 px-4 text-sm">
                      <div className="min-w-0 overflow-hidden rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
                        <div className="text-xs text-[var(--color-text-tertiary)]">分类</div>
                        <div className="mt-1 truncate font-medium text-[var(--color-text-primary)]">
                          {skill.category}
                        </div>
                      </div>
                      <div className="min-w-0 overflow-hidden rounded-md bg-[var(--color-bg-secondary)] px-3 py-2">
                        <div className="text-xs text-[var(--color-text-tertiary)]">状态</div>
                        <div className="mt-1 flex min-w-0 items-center gap-1 truncate font-medium text-[var(--color-text-primary)]">
                          {installed && skill.enabled && <CheckCircle2 size={14} color="var(--color-success)" />}
                          {installed ? (skill.enabled ? '已启用' : '已停用') : '未安装'}
                        </div>
                      </div>
                    </div>

                    <div className="line-clamp-1 min-h-0 overflow-hidden px-4 py-1 text-xs text-[var(--color-text-secondary)]">
                      {skill.trigger || skill.id}
                    </div>

                    <div aria-hidden="true" />

                    <div className="flex min-h-0 items-center justify-between gap-3 self-end px-4 py-3">
                      {installed ? (
                        <ToggleButton
                          checked={skill.enabled}
                          checkedLabel="启用中"
                          uncheckedLabel="已停用"
                          disabled={busy}
                          ariaLabel={`${skill.name} 启停状态`}
                          onChange={(enabled) => runSkillAction(
                            skill.id,
                            () => setSkillEnabled(skill.id, enabled),
                          )}
                        />
                      ) : (
                        <span className="text-xs text-[var(--color-text-tertiary)]">
                          安装后可在 Agent 中选择
                        </span>
                      )}

                      <div className="flex shrink-0 gap-2">
                        <Button onClick={() => setSelectedSkill(skill)}>
                          详情
                        </Button>
                        <Button
                          loading={busy}
                          onClick={() => handleExportSkill(skill)}
                          aria-label="导出 Skill"
                        >
                          <Download size={14} />
                        </Button>
                        {installed ? (
                          <Button
                            danger
                            loading={busy}
                            onClick={() => runSkillAction(
                              skill.id,
                              () => uninstallSkill(skill.id),
                            )}
                          >
                            卸载
                          </Button>
                        ) : (
                          <Button
                            type="primary"
                            loading={busy}
                            onClick={() => runSkillAction(
                              skill.id,
                              () => installSkill(skill.id),
                            )}
                          >
                            安装
                          </Button>
                        )}
                        <Button
                          type="text"
                          danger
                          loading={busy}
                          onClick={() => setDeleteConfirm(skill)}
                          aria-label="删除 Skill"
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </main>

      <Modal
        title={selectedSkill?.name}
        open={Boolean(selectedSkill)}
        onCancel={() => setSelectedSkill(null)}
        footer={null}
        width={720}
      >
        {selectedSkill && (
          <div className="space-y-5">
            <div className="flex items-start justify-between gap-3">
              <p className="min-w-0 text-sm text-[var(--color-text-secondary)]">
                {selectedSkill.description}
              </p>
              <Button
                loading={busyId === selectedSkill.id}
                onClick={() => handleExportSkill(selectedSkill)}
              >
                <span className="inline-flex items-center gap-1.5">
                  <Download size={14} className="shrink-0" />
                  导出
                </span>
              </Button>
            </div>

            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-[var(--color-text-tertiary)]">分类</div>
                <div className="mt-1 text-[var(--color-text-primary)]">{selectedSkill.category}</div>
              </div>
              <div>
                <div className="text-xs text-[var(--color-text-tertiary)]">状态</div>
                <div className="mt-1 text-[var(--color-text-primary)]">
                  {selectedSkill.status === 'installed'
                    ? (selectedSkill.enabled ? '已启用' : '已停用')
                    : '未安装'}
                </div>
              </div>
              <div className="col-span-2">
                <div className="text-xs text-[var(--color-text-tertiary)]">触发条件</div>
                <div className="mt-1 rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-sm text-[var(--color-text-primary)]">
                  {selectedSkill.trigger || '未配置'}
                </div>
              </div>
              <div className="col-span-2">
                <div className="text-xs text-[var(--color-text-tertiary)]">内容</div>
                <pre className="mt-1 max-h-[260px] overflow-auto rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
                  {selectedSkill.content || '未配置'}
                </pre>
              </div>
              {selectedSkill.resources.length > 0 && (
                <div className="col-span-2">
                  <div className="text-xs text-[var(--color-text-tertiary)]">资源文件</div>
                  <div className="mt-1 max-h-[180px] overflow-auto rounded-md bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
                    <div className="mb-2 text-[var(--color-text-tertiary)]">
                      {selectedSkill.resourcePath}
                    </div>
                    <div className="space-y-1">
                      {selectedSkill.resources.map((resource) => (
                        <div key={resource}>{resource}</div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </Modal>

      <Modal
        title="删除 Skill"
        open={Boolean(deleteConfirm)}
        onCancel={() => setDeleteConfirm(null)}
        onOk={handleDeleteSkill}
        confirmLoading={Boolean(deleteConfirm && busyId === deleteConfirm.id)}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
      >
        确定要删除「{deleteConfirm?.name}」吗？内置 Skill 不能删除，只能卸载或停用。
      </Modal>

      <SkillRegisterModal
        open={registerOpen}
        form={registerForm}
        error={registerError}
        registering={registering}
        onFormChange={setRegisterForm}
        onCancel={() => setRegisterOpen(false)}
        onRegister={() => void handleRegister()}
      />
    </div>
  );
}
