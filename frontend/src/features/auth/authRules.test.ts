import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { AgentGuardrailsSection } from '../agents/sections/AgentGuardrailsSection';
import { adminUserActionPath } from './authClient';
import { shouldPromptLocalApiKey, showAdminEntry, workbenchGate } from './authRules';
import { DESKTOP_CAPABILITIES } from './authRules';
import { useAuthStore } from './authStore';
import { AdminUsersPanel } from './AdminUsersPanel';
import { AuthScreen } from './AuthScreen';
import type { AgentCreatePayload } from '../../types/agent';

describe('hosted auth gate', () => {
  it('blocks the hosted workbench until login and leaves desktop open', () => {
    expect(workbenchGate('hosted', null)).toBe('login');
    expect(workbenchGate('hosted', { id: 'u', email: 'a@b.c', role: 'user', status: 'active' })).toBe('workbench');
    expect(workbenchGate('desktop', null)).toBe('workbench');
  });

  it('prompts for a local API key only when the desktop client is not logged in', () => {
    expect(shouldPromptLocalApiKey('desktop', false)).toBe(true);
    expect(shouldPromptLocalApiKey('desktop', true)).toBe(false);
    expect(shouldPromptLocalApiKey('hosted', false)).toBe(false);
    expect(shouldPromptLocalApiKey('hosted', true)).toBe(false);
  });

  it('shows the admin entry only for admins', () => {
    expect(showAdminEntry(null)).toBe(false);
    expect(showAdminEntry({ id: 'u', email: 'a@b.c', role: 'user', status: 'active' })).toBe(false);
    expect(showAdminEntry({ id: 'u', email: 'a@b.c', role: 'admin', status: 'active' })).toBe(true);
  });

  it('renders login instead of the workbench', () => {
    const html = renderToStaticMarkup(createElement(AuthScreen, {
      mode: 'login',
      email: '',
      password: '',
      code: '',
      error: '',
      onEmail: () => undefined,
      onPassword: () => undefined,
      onCode: () => undefined,
      onMode: () => undefined,
      onSubmit: () => undefined,
    }));
    expect(html).toContain('登录');
    expect(html).toContain('name@example.com');
    expect(html).toContain('至少 8 位');
    expect(html).not.toContain('对话工作台');
  });

  it('admin panel disables a user through the callback', () => {
    const onDisable = vi.fn();
    const html = renderToStaticMarkup(createElement(AdminUsersPanel, {
      summary: { total: 1, online: 1, pending: 0, disabled: 0 },
      users: [{
        id: 'usr_1',
        email: 'member@example.com',
        role: 'user',
        status: 'active',
        createdAt: '2026-10-08T00:00:00Z',
        lastSeenAt: '2026-10-08T00:00:30Z',
        online: true,
        clients: ['web', 'desktop'],
      }],
      onDisable,
      onEnable: () => undefined,
      onDelete: () => undefined,
    }));
    expect(html).toContain('member@example.com');
    expect(html).toContain('在线');
    expect(html).toContain('Web');
    expect(html).toContain('桌面');
    expect(html).not.toContain('普通用户看不到');
    expect(html).toContain('删 除');
    expect(adminUserActionPath('usr_1', 'disable')).toBe('/api/admin/users/usr_1/disable');
    expect(adminUserActionPath('usr_1', 'delete')).toBe('/api/admin/users/usr_1');
  });

  it('hides local execution choices when the hosted capability is off', () => {
    useAuthStore.setState({
      capabilities: { ...DESKTOP_CAPABILITIES, localExecution: false, sandboxImages: ['python:3.11-slim'] },
    });
    const form = {
      name: '助手',
      description: '',
      systemPrompt: '',
      avatar: '',
      tags: [],
      skills: [],
      mcpTools: [],
      knowledgeTools: [],
      guardrails: {
        filesystemScope: '',
        shellBlacklist: [],
        networkEnabled: true,
        humanApproval: false,
        toolPermissionMode: 'accept_edits',
        toolPermissionProfile: 'confirm_destructive',
        executionBackend: 'local',
        dockerImage: 'python:3.11-slim',
        persistentSandbox: false,
        timeoutSeconds: 300,
      },
    } as unknown as AgentCreatePayload;
    const html = renderToStaticMarkup(createElement(AgentGuardrailsSection, {
      form,
      onChange: () => undefined,
      forceSandbox: true,
    }));
    expect(html).toContain('Web 端命令在隔离沙箱中执行');
    expect(html).not.toContain('本地工作区');
  });
});
