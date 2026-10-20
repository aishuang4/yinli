import { describe, expect, it } from 'vitest';
import { useAgentsStore } from './agentsStore';
import { useAppStore } from './appStore';
import { useAuthStore } from '../features/auth/authStore';
import { resetTenantState } from './resetTenantState';

describe('resetTenantState', () => {
  it('clears workspace context and agents when the signed-in user changes', () => {
    useAppStore.setState({ activeProjectId: 'project-a', activeWorkspaceId: 'workspace-a' });
    useAgentsStore.setState({ agents: [{ id: 'agent-a' } as never], revision: 3 });
    useAuthStore.setState({
      loaded: true,
      user: { id: 'usr_a', email: 'a@example.com', role: 'user', status: 'active' },
    });
    useAuthStore.getState().setSession('hosted', null, false);
    expect(useAppStore.getState().activeProjectId).toBe('default');
    expect(useAgentsStore.getState().agents).toEqual([]);
    useAppStore.setState({ activeWorkspaceId: 'workspace-b' });
    resetTenantState();
    expect(useAppStore.getState().activeWorkspaceId).toBe('default');
  });
});
