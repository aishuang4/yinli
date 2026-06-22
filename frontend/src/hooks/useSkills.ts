/**
 * useSkills — Skills 浏览和安装管理 Hook
 *
 * 封装 /api/skills 相关调用，供 Skills Browser 和 AgentEditor 复用。
 */

import { useCallback, useEffect, useState } from 'react';
import type { Skill, SkillRegisterPayload } from '../types/skill';
import { getErrorMessage, notifyError, notifySuccess } from '../utils/feedback';

interface UseSkillsReturn {
  skills: Skill[];
  categories: string[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  registerSkill: (payload: SkillRegisterPayload) => Promise<Skill>;
  importSkillArchive: (file: File) => Promise<Skill>;
  installSkill: (id: string) => Promise<Skill>;
  uninstallSkill: (id: string) => Promise<Skill>;
  deleteSkill: (id: string) => Promise<void>;
  setSkillEnabled: (id: string, enabled: boolean) => Promise<Skill>;
}

interface UseEnabledSkillsReturn {
  skills: Skill[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function useSkills(): UseSkillsReturn {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [skillList, categoryList] = await Promise.all([
        requestJson<Skill[]>('/api/skills'),
        requestJson<string[]>('/api/skills/categories'),
      ]);
      setSkills(skillList);
      setCategories(categoryList);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load skills');
    } finally {
      setLoading(false);
    }
  }, []);

  const updateSkill = useCallback((skill: Skill) => {
    setSkills((prev) => {
      if (prev.some((item) => item.id === skill.id)) {
        return prev.map((item) => (item.id === skill.id ? skill : item));
      }
      return [...prev, skill];
    });
  }, []);

  const registerSkill = useCallback(async (payload: SkillRegisterPayload) => {
    try {
      const skill = await requestJson<Skill>('/api/skills/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      updateSkill(skill);
      setCategories((prev) => (
        prev.includes(skill.category) ? prev : [...prev, skill.category].sort()
      ));
      notifySuccess('Skill 注册成功');
      return skill;
    } catch (err) {
      notifyError(`Skill 注册失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [updateSkill]);

  const importSkillArchive = useCallback(async (file: File) => {
    try {
      const formData = new FormData();
      formData.append('file', file);
      const skill = await requestJson<Skill>('/api/skills/import', {
        method: 'POST',
        body: formData,
      });
      updateSkill(skill);
      setCategories((prev) => (
        prev.includes(skill.category) ? prev : [...prev, skill.category].sort()
      ));
      notifySuccess('Skill 导入成功');
      return skill;
    } catch (err) {
      notifyError(`Skill 导入失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [updateSkill]);

  const installSkill = useCallback(async (id: string) => {
    try {
      const skill = await requestJson<Skill>(`/api/skills/${id}/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true }),
      });
      updateSkill(skill);
      notifySuccess('Skill 安装成功');
      return skill;
    } catch (err) {
      notifyError(`Skill 安装失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [updateSkill]);

  const uninstallSkill = useCallback(async (id: string) => {
    try {
      const skill = await requestJson<Skill>(`/api/skills/${id}/uninstall`, {
        method: 'POST',
      });
      updateSkill(skill);
      notifySuccess('Skill 卸载成功');
      return skill;
    } catch (err) {
      notifyError(`Skill 卸载失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [updateSkill]);

  const deleteSkill = useCallback(async (id: string) => {
    try {
      const response = await fetch(`/api/skills/${id}`, { method: 'DELETE' });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }
      setSkills((prev) => prev.filter((skill) => skill.id !== id));
      notifySuccess('Skill 删除成功');
    } catch (err) {
      notifyError(`Skill 删除失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, []);

  const setSkillEnabled = useCallback(async (id: string, enabled: boolean) => {
    try {
      const skill = await requestJson<Skill>(`/api/skills/${id}/enable`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      updateSkill(skill);
      notifySuccess(enabled ? 'Skill 已启用' : 'Skill 已停用');
      return skill;
    } catch (err) {
      notifyError(`Skill 状态更新失败：${getErrorMessage(err, '未知错误')}`);
      throw err;
    }
  }, [updateSkill]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    skills,
    categories,
    loading,
    error,
    refresh,
    registerSkill,
    importSkillArchive,
    installSkill,
    uninstallSkill,
    deleteSkill,
    setSkillEnabled,
  };
}

export function useEnabledSkills(): UseEnabledSkillsReturn {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setSkills(await requestJson<Skill[]>('/api/skills/enabled'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load enabled skills');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { skills, loading, error, refresh };
}
