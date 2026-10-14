/** 账号接口。Cookie 由浏览器携带，桌面令牌留在本机后端。 */

import type { AuthUser, CloudAccount, ProductCapabilities, ServeMode } from './authRules';
import { DESKTOP_CAPABILITIES, EMPTY_CLOUD_ACCOUNT } from './authRules';
import type { AdminSummary, AdminUserRow } from './AdminUsersPanel';

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload as T;
}

export async function fetchAuthConfig(): Promise<{ serveMode: ServeMode; capabilities: ProductCapabilities }> {
  const payload = await readJson<{ serveMode?: string; capabilities?: Partial<ProductCapabilities> }>(
    await fetch('/api/auth/config', { credentials: 'include' }),
  );
  const serveMode: ServeMode = payload.serveMode === 'hosted' ? 'hosted' : 'desktop';
  return {
    serveMode,
    capabilities: { ...DESKTOP_CAPABILITIES, ...(payload.capabilities || {}) },
  };
}

export async function fetchDesktopSession(): Promise<CloudAccount> {
  const response = await fetch('/api/auth/desktop/session', { credentials: 'include' });
  if (!response.ok) return EMPTY_CLOUD_ACCOUNT;
  const payload = await readJson<Partial<CloudAccount>>(response);
  return {
    connected: Boolean(payload.connected),
    email: payload.email || '',
    status: payload.status || '',
    baseUrl: payload.baseUrl || '',
  };
}

export async function fetchCurrentUser(): Promise<AuthUser | null> {
  const response = await fetch('/api/auth/me', { credentials: 'include' });
  if (response.status === 401) return null;
  const payload = await readJson<{ user: AuthUser }>(response);
  return payload.user;
}

export async function logout(): Promise<void> {
  await readJson(await fetch('/api/auth/logout', {
    method: 'POST',
    credentials: 'include',
  }));
}

export async function submitAuth(path: string, body: Record<string, string>): Promise<unknown> {
  return readJson(await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

export function adminUserActionPath(userId: string, action: 'disable' | 'enable' | 'delete'): string {
  const id = encodeURIComponent(userId);
  return action === 'delete' ? `/api/admin/users/${id}` : `/api/admin/users/${id}/${action}`;
}

export async function postAdminUserAction(userId: string, action: 'disable' | 'enable' | 'delete'): Promise<void> {
  await readJson(await fetch(adminUserActionPath(userId, action), {
    method: action === 'delete' ? 'DELETE' : 'POST',
    credentials: 'include',
  }));
}

export async function fetchAdminUsers(): Promise<{ summary: AdminSummary; users: AdminUserRow[] }> {
  return readJson(await fetch('/api/admin/users', { credentials: 'include' }));
}
