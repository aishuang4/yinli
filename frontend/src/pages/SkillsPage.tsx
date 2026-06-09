/**
 * SkillsPage — Skills 浏览器页面
 *
 * 支持浏览 Skills、按分类/安装状态筛选、注册自定义 Skill、安装/卸载、启停和查看详情。
 */

import { useMemo, useRef, useState } from 'react';
import { CheckCircle2, Package, Plus, RefreshCw, Search, Upload } from 'lucide-react';
import {
  Button,
  Card,
  Empty,
  Input,
  Modal,
  Select,
  Spinner,
  Switch,
  Tag,
} from '@agentscope-ai/design';
import { useSkills } from '../hooks/useSkills';
import type { Skill } from '../types/skill';

type InstallFilter = 'all' | 'installed' | 'available';

const installFilterOptions = [
  { label: '全部', value: 'all' },
  { label: '已安装', value: 'installed' },
  { label: '可安装', value: 'available' },
];

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

export function SkillsPage() {
  const {
    skills,
    categories,
    loading,
    error,
    refresh,
    registerSkill,
    importSkillArchive,
    installSkill,
    uninstallSkill,
    setSkillEnabled,
  } = useSkills();

  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [installFilter, setInstallFilter] = useState<InstallFilter>('all');
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [importing, setImporting] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [registerForm, setRegisterForm] = useState({
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

  const categoryOptions = [
    { label: '全部分类', value: 'all' },
    ...categories.map((item) => ({ label: item, value: item })),
  ];

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
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
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
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,application/zip"
              className="hidden"
              onChange={(event) => handleImportArchive(event.target.files?.[0])}
            />
            <Button
              loading={importing}
              onClick={() => {
                setImportError(null);
                fileInputRef.current?.click();
              }}
            >
              <span className="inline-flex items-center gap-1.5">
                <Upload size={14} className="shrink-0" />
                导入压缩包
              </span>
            </Button>
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
        <div className="grid grid-cols-[minmax(240px,1fr)_180px_160px] gap-3">
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 Skill、触发条件或标签"
            prefix={<Search size={14} />}
            allowClear
          />
          <Select
            value={category}
            onChange={setCategory}
            options={categoryOptions}
          />
          <Select
            value={installFilter}
            onChange={setInstallFilter}
            options={installFilterOptions}
          />
        </div>
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
                  className="h-[400px] overflow-hidden"
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
                        <Switch
                          checked={skill.enabled}
                          label={skill.enabled ? '启用中' : '已停用'}
                          disabled={busy}
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
            <p className="text-sm text-[var(--color-text-secondary)]">
              {selectedSkill.description}
            </p>

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
        title="注册 Skill"
        open={registerOpen}
        onCancel={() => setRegisterOpen(false)}
        onOk={handleRegister}
        confirmLoading={registering}
        okText="注册"
        cancelText="取消"
        width={640}
      >
        <div className="space-y-4">
          {registerError && (
            <div className="rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] p-3 text-sm text-[var(--color-error)]">
              {registerError}
            </div>
          )}

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              名称 <span className="text-[var(--color-error)]">*</span>
            </label>
            <Input
              value={registerForm.name}
              onChange={(event) => setRegisterForm({
                ...registerForm,
                name: event.target.value,
              })}
              placeholder="如：API 文档撰写"
              maxLength={80}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
                分类
              </label>
              <Input
                value={registerForm.category}
                onChange={(event) => setRegisterForm({
                  ...registerForm,
                  category: event.target.value,
                })}
                placeholder="自定义"
                maxLength={40}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
                标签
              </label>
              <Input
                value={registerForm.tags}
                onChange={(event) => setRegisterForm({
                  ...registerForm,
                  tags: event.target.value,
                })}
                placeholder="docs, api, writing"
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              描述
            </label>
            <Input.TextArea
              value={registerForm.description}
              onChange={(event) => setRegisterForm({
                ...registerForm,
                description: event.target.value,
              })}
              placeholder="说明这个 Skill 的能力"
              rows={2}
              maxLength={300}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              触发条件
            </label>
            <Input
              value={registerForm.trigger}
              onChange={(event) => setRegisterForm({
                ...registerForm,
                trigger: event.target.value,
              })}
              placeholder="当用户要求撰写 API 文档时触发"
              maxLength={200}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-secondary)]">
              Skill 内容
            </label>
            <Input.TextArea
              value={registerForm.content}
              onChange={(event) => setRegisterForm({
                ...registerForm,
                content: event.target.value,
              })}
              placeholder="写入具体执行要求、输出格式、注意事项..."
              rows={6}
              maxLength={12000}
              style={{ fontFamily: 'var(--font-mono)' }}
            />
          </div>
        </div>
      </Modal>
    </div>
  );
}
