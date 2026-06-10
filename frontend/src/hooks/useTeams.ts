import { useCallback, useEffect, useState } from 'react';
import type { TeamCreatePayload, TeamRecord, TeamUpdatePayload } from '../types/team';

interface UseTeamsReturn {
  teams: TeamRecord[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createTeam: (data: TeamCreatePayload) => Promise<TeamRecord>;
  updateTeam: (id: string, data: TeamUpdatePayload) => Promise<TeamRecord>;
  deleteTeam: (id: string) => Promise<void>;
}

export function useTeams(): UseTeamsReturn {
  const [teams, setTeams] = useState<TeamRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch('/api/teams');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setTeams(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load teams');
    } finally {
      setLoading(false);
    }
  }, []);

  const createTeam = useCallback(async (data: TeamCreatePayload) => {
    const response = await fetch('/api/teams', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    const team: TeamRecord = await response.json();
    setTeams((current) => [team, ...current]);
    return team;
  }, []);

  const deleteTeam = useCallback(async (id: string) => {
    const response = await fetch(`/api/teams/${id}`, { method: 'DELETE' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    setTeams((current) => current.filter((team) => team.id !== id));
  }, []);

  const updateTeam = useCallback(async (id: string, data: TeamUpdatePayload) => {
    const response = await fetch(`/api/teams/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    const team: TeamRecord = await response.json();
    setTeams((current) => current.map((item) => (item.id === id ? team : item)));
    return team;
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { teams, loading, error, refresh, createTeam, updateTeam, deleteTeam };
}
