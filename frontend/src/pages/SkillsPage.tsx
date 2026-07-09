/**
 * SkillsPage — Skills 浏览器页面。
 *
 * 页面只负责数据编排、筛选状态和弹窗动作；列表和详情交给 features/skills 组件。
 */

import { useMemo, useState } from 'react';
import { Modal } from '@agentscope-ai/design';
import { SkillDetailPage } from '../features/skills/SkillDetailPage';
import { SkillLandingPage } from '../features/skills/SkillLandingPage';
import { SkillRegisterModal, type SkillRegisterFormState } from '../features/skills/SkillRegisterModal';
import type { CatalogInstallFilter } from '../components/form/CatalogFilterBar';
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

  const modals = (
    <>
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
    </>
  );

  if (selectedSkill) {
    return (
      <>
        <SkillDetailPage
          skill={selectedSkill}
          busy={busyId === selectedSkill.id}
          onBack={() => setSelectedSkill(null)}
          onExport={() => void handleExportSkill(selectedSkill)}
          onInstall={() => void runSkillAction(selectedSkill.id, () => installSkill(selectedSkill.id))}
          onUninstall={() => void runSkillAction(selectedSkill.id, () => uninstallSkill(selectedSkill.id))}
          onDelete={() => setDeleteConfirm(selectedSkill)}
          onToggle={(enabled) => void runSkillAction(
            selectedSkill.id,
            () => setSkillEnabled(selectedSkill.id, enabled),
          )}
        />
        {modals}
      </>
    );
  }

  return (
    <>
      <SkillLandingPage
        skills={filteredSkills}
        totalCount={skills.length}
        installedCount={installedCount}
        enabledCount={enabledCount}
        categories={categories}
        loading={loading}
        error={error}
        importError={importError}
        importing={importing}
        busyId={busyId}
        query={query}
        category={category}
        installFilter={installFilter}
        onQueryChange={setQuery}
        onCategoryChange={setCategory}
        onInstallFilterChange={setInstallFilter}
        onRefresh={() => void refresh()}
        onRegisterOpen={() => {
          setRegisterError(null);
          setRegisterOpen(true);
        }}
        onImportArchive={(file) => {
          setImportError(null);
          void handleImportArchive(file);
        }}
        onSelectSkill={setSelectedSkill}
        onExportSkill={(skill) => void handleExportSkill(skill)}
        onInstallSkill={(skill) => void runSkillAction(skill.id, () => installSkill(skill.id))}
        onUninstallSkill={(skill) => void runSkillAction(skill.id, () => uninstallSkill(skill.id))}
        onDeleteSkill={setDeleteConfirm}
        onToggleSkill={(skill, enabled) => void runSkillAction(skill.id, () => setSkillEnabled(skill.id, enabled))}
      />
      {modals}
    </>
  );
}
