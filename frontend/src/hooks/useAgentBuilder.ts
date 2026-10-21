import { useCallback, useState } from 'react';
import type { BuilderApplyResponse, BuilderPlan, BuilderPlanPayload } from '../types/agentBuilder';
import { useAppStore } from '../stores/appStore';

interface UseAgentBuilderReturn {
  planning: boolean;
  applying: boolean;
  error: string | null;
  createPlan: (payload: BuilderPlanPayload) => Promise<BuilderPlan>;
  applyPlan: (plan: BuilderPlan) => Promise<BuilderApplyResponse>;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function useAgentBuilder(): UseAgentBuilderReturn {
    const activeProjectSlug = useAppStore((state) => state.activeProjectSlug);
  const [planning, setPlanning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const createPlan = useCallback(async (payload: BuilderPlanPayload) => {
    try {
      setPlanning(true);
      setError(null);
      return await requestJson<BuilderPlan>('/api/agent-builder/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          requirement: payload.requirement,
          preferredModel: payload.preferredModel,
        }),
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : '智能编排失败';
      setError(message);
      throw err;
    } finally {
      setPlanning(false);
    }
  }, []);

  const applyPlan = useCallback(async (plan: BuilderPlan) => {
    try {
      setApplying(true);
      setError(null);
      return await requestJson<BuilderApplyResponse>('/api/agent-builder/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          plan,
          
          projectSlug: activeProjectSlug,
        }),
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : '创建失败';
      setError(message);
      throw err;
    } finally {
      setApplying(false);
    }
  }, [activeProjectSlug]);

  return { planning, applying, error, createPlan, applyPlan };
}
