import { describe, expect, it } from 'vitest';
import { useAgentsStore } from './agentsStore';
import { useAppStore } from './appStore';
import { useAuthStore } from '../features/auth/authStore';
import { resetTenantState } from './resetTenantState';

describe('resetTenantState', () => {
  it('clears chat view and agents when the signed-in user changes', () => {
    useAppStore.setState({ currentView: 'skills', pendingJobId: 'job-1' });
    useAgentsStore.setState({ agents: [{ id: 'agent-a' } as never], revision: 3 });
    useAuthStore.setState({
      loaded: true,
      user: { id: 'usr_a', email: 'a@example.com', role: 'user', status: 'active' },
    });
    useAuthStore.getState().setSession('hosted', null, false);
    expect(useAppStore.getState().currentView).toBe('chat');
    expect(useAgentsStore.getState().agents).toEqual([]);
    useAppStore.setState({ currentView: 'memory' });
    resetTenantState();
    expect(useAppStore.getState().currentView).toBe('chat');
    expect(useAppStore.getState().pendingJobId).toBeNull();
  });
});
