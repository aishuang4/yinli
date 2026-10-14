/** 管理员看到的用户、在线端和停用操作。 */

import { useState } from 'react';
import { Button } from '@/components/design';
import { WorkbenchListPage } from '../../components/workbench/WorkbenchListPage';

export type AdminUserRow = {
  id: string;
  email: string;
  role: string;
  status: string;
  createdAt: string;
  lastSeenAt: string | null;
  online: boolean;
  clients: string[];
};

export type AdminSummary = {
  total: number;
  online: number;
  pending: number;
  disabled: number;
};

const CLIENT_LABEL: Record<string, string> = {
  web: 'Web',
  desktop: '桌面',
};

const STATUS_LABEL: Record<string, string> = {
  pending: '待验证',
  active: '正常',
  disabled: '已停用',
};

function formatSeen(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

export function AdminUsersPanel({
  summary,
  users,
  currentUserId,
  onDisable,
  onEnable,
  onDelete,
}: {
  summary: AdminSummary;
  users: AdminUserRow[];
  currentUserId?: string;
  onDisable: (userId: string) => void;
  onEnable: (userId: string) => void;
  onDelete: (userId: string) => void;
}) {
  const [tab, setTab] = useState<'users' | 'models'>('users');
  const stats = [
    { label: '注册用户', value: summary.total },
    { label: '当前在线', value: summary.online },
    { label: '待验证', value: summary.pending },
    { label: '已停用', value: summary.disabled },
  ];
  return (
    <WorkbenchListPage title="用户管理">
      <div className="mb-3 flex gap-2">
        <Button size="small" type={tab === 'users' ? 'primary' : 'default'} onClick={() => setTab('users')}>用户</Button>
        <Button size="small" type={tab === 'models' ? 'primary' : 'default'} onClick={() => setTab('models')}>平台模型</Button>
      </div>
      {tab === 'models' ? <PlatformCapabilityEditor /> : (
      <section className="admin-users" aria-label="用户管理">
        <div className="admin-users__stats">
          {stats.map((item) => (
            <article key={item.label} className="admin-users__stat">
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </article>
          ))}
        </div>
        <div className="admin-users__table-wrap">
          <table className="admin-users__table">
            <thead>
              <tr>
                <th>邮箱</th>
                <th>角色</th>
                <th>状态</th>
                <th>在线</th>
                <th>最近活跃</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 ? (
                <tr>
                  <td colSpan={6} className="admin-users__empty">还没有用户</td>
                </tr>
              ) : users.map((user) => {
                const isSelf = user.id === currentUserId;
                return (
                  <tr key={user.id}>
                    <td>{user.email}</td>
                    <td>{user.role === 'admin' ? '管理员' : '用户'}</td>
                    <td>{STATUS_LABEL[user.status] || user.status}</td>
                    <td>
                      {user.online ? (
                        <span className="admin-users__online">
                          在线
                          {user.clients.length > 0 ? ` · ${user.clients.map((item) => CLIENT_LABEL[item] || item).join(' / ')}` : ''}
                        </span>
                      ) : '离线'}
                    </td>
                    <td>{formatSeen(user.lastSeenAt)}</td>
                    <td>
                      {isSelf ? (
                        <span className="admin-users__self">当前账号</span>
                      ) : (
                        <span className="admin-users__actions">
                          {user.status === 'disabled' ? (
                            <Button size="small" onClick={() => onEnable(user.id)}>恢复</Button>
                          ) : (
                            <Button size="small" onClick={() => onDisable(user.id)}>停用</Button>
                          )}
                          <Button size="small" onClick={() => onDelete(user.id)}>删除</Button>
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      )}
    </WorkbenchListPage>
  );
}

function PlatformCapabilityEditor() {
  const [text, setText] = useState('{"version":1,"models":[]}');
  const [message, setMessage] = useState('');

  const load = async () => {
    const response = await fetch('/api/admin/platform-models', { credentials: 'include' });
    if (!response.ok) {
      setMessage('无法读取平台模型能力表');
      return;
    }
    setText(JSON.stringify(await response.json(), null, 2));
  };

  const save = async () => {
    setMessage('');
    const response = await fetch('/api/admin/platform-models', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: text,
    });
    setMessage(response.ok ? '已保存' : '保存失败');
  };

  return (
    <section className="space-y-2" aria-label="平台模型">
      <p className="text-xs text-[var(--color-text-secondary)]">只改能力表。连接和 Key 不在这里。</p>
      <textarea className="h-48 w-full rounded-md border border-[var(--color-border)] p-2 font-mono text-xs" value={text} onChange={(event) => setText(event.target.value)} />
      <div className="flex gap-2">
        <Button size="small" onClick={() => void load()}>读取</Button>
        <Button size="small" type="primary" onClick={() => void save()}>保存</Button>
      </div>
      {message ? <p className="text-xs">{message}</p> : null}
    </section>
  );
}
