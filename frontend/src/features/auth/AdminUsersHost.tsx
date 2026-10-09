/** 管理员打开用户页时替换工作台内容，普通用户没有入口。 */

import { useEffect, useState, type ReactNode } from 'react';
import { AdminUsersPanel, type AdminSummary, type AdminUserRow } from './AdminUsersPanel';
import { fetchAdminUsers, postAdminUserAction } from './authClient';
import { showAdminEntry } from './authRules';
import { useAuthStore } from './authStore';

const EMPTY_SUMMARY: AdminSummary = { total: 0, online: 0, pending: 0, disabled: 0 };

export function AdminUsersHost({ children }: { children: ReactNode }) {
  const user = useAuthStore((state) => state.user);
  const adminOpen = useAuthStore((state) => state.adminOpen);
  const [summary, setSummary] = useState<AdminSummary>(EMPTY_SUMMARY);
  const [users, setUsers] = useState<AdminUserRow[]>([]);
  const visible = showAdminEntry(user) && adminOpen;

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    const load = () => {
      void fetchAdminUsers().then((payload) => {
        if (cancelled) return;
        setSummary(payload.summary);
        setUsers(payload.users);
      }).catch(() => undefined);
    };
    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [visible]);

  if (!visible) return children;
  return (
    <AdminUsersPanel
      summary={summary}
      users={users}
      currentUserId={user?.id}
      onDisable={(userId) => {
        void postAdminUserAction(userId, 'disable').then(() => fetchAdminUsers()).then((payload) => {
          setSummary(payload.summary);
          setUsers(payload.users);
        });
      }}
      onEnable={(userId) => {
        void postAdminUserAction(userId, 'enable').then(() => fetchAdminUsers()).then((payload) => {
          setSummary(payload.summary);
          setUsers(payload.users);
        });
      }}
      onDelete={(userId) => {
        const target = users.find((item) => item.id === userId);
        const email = target?.email || '该用户';
        if (!window.confirm(`删除 ${email}？该账号将不能再登录，此操作不能恢复。`)) return;
        void postAdminUserAction(userId, 'delete').then(() => fetchAdminUsers()).then((payload) => {
          setSummary(payload.summary);
          setUsers(payload.users);
        });
      }}
    />
  );
}
