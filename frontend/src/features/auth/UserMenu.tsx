/** 右上角用户中心：基本信息，以及设置、用户管理等常用入口。 */

import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { KeyRound, LogOut, Settings, Users } from 'lucide-react';
import { logout, submitAuth } from './authClient';
import { EMPTY_CLOUD_ACCOUNT, showAdminEntry } from './authRules';
import { useAuthStore } from './authStore';
import { useAppStore } from '../../stores/appStore';

export function UserMenu({ className = '' }: { className?: string }) {
  const user = useAuthStore((state) => state.user);
  const serveMode = useAuthStore((state) => state.serveMode);
  const setSession = useAuthStore((state) => state.setSession);
  const setAdminOpen = useAuthStore((state) => state.setAdminOpen);
  const setView = useAppStore((state) => state.setView);
  const [open, setOpen] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({});
  const [usage, setUsage] = useState<{ tokenQuota: number; used: number; remaining: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const trigger = rootRef.current?.querySelector('button');
    if (trigger) {
      const rect = trigger.getBoundingClientRect();
      setPanelStyle({
        top: rect.bottom + 8,
        right: Math.max(12, window.innerWidth - rect.right),
      });
    }
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    window.addEventListener('pointerdown', onPointerDown);
    return () => window.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setUsage(null);
    void fetch('/api/auth/usage', { credentials: 'include' })
      .then(async (response) => (response.ok ? response.json() : null))
      .then((payload: { available?: boolean; tokenQuota?: number; used?: number; remaining?: number } | null) => {
        if (!cancelled && payload?.available) {
          setUsage({
            tokenQuota: Number(payload.tokenQuota || 0),
            used: Number(payload.used || 0),
            remaining: Number(payload.remaining || 0),
          });
        }
      })
      .catch(() => {
        if (!cancelled) setUsage(null);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (serveMode === 'desktop') {
    return <DesktopCloudEntry className={className} />;
  }
  if (!user || serveMode !== 'hosted') {
    return (
      <button
        type="button"
        className={className}
        aria-label="进入工作台设置"
        onClick={() => setView('settings')}
      >
        <Settings size={17} />
      </button>
    );
  }

  const close = () => setOpen(false);
  const initial = user.email.slice(0, 1).toUpperCase();
  const roleLabel = user.role === 'admin' ? '管理员' : '用户';
  const clientLabel = typeof window !== 'undefined' && window.electronAPI ? '桌面端' : 'Web';

  const signOut = async () => {
    try {
      await logout();
    } catch {
      // 会话已经失效时仍然回到登录页。
    }
    setAdminOpen(false);
    setSession('hosted', null, false);
  };

  const openSettings = () => {
    setAdminOpen(false);
    setView('settings');
    close();
  };

  const openAdmin = () => {
    setAdminOpen(true);
    close();
  };

  const changePassword = async () => {
    setMessage('');
    if (!code.trim()) {
      await submitAuth('/api/auth/forgot-password', { email: user.email });
      setMessage('验证码已发送。开发环境请看后端日志。');
      return;
    }
    await submitAuth('/api/auth/reset-password', {
      email: user.email,
      code: code.trim(),
      password,
    });
    setAdminOpen(false);
    setSession('hosted', null, false);
  };

  return (
    <div className="titlebar__user" ref={rootRef}>
      <button
        type="button"
        className={className}
        aria-expanded={open}
        aria-label="用户中心"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="titlebar__user-avatar" aria-hidden="true">{initial}</span>
      </button>
      {open ? createPortal((
        <div ref={panelRef} className="titlebar__user-menu" style={panelStyle} role="menu">
          <div className="titlebar__user-card">
            <span className="titlebar__user-avatar titlebar__user-avatar--lg" aria-hidden="true">{initial}</span>
            <div className="min-w-0">
              <p className="titlebar__user-email">{user.email}</p>
              <p className="titlebar__user-meta">
                <span className={`titlebar__user-role${user.role === 'admin' ? ' titlebar__user-role--admin' : ''}`}>{roleLabel}</span>
                <span className="titlebar__user-presence">{clientLabel} · 在线</span>
              </p>
            </div>
          </div>
          {usage ? (
            <div className="titlebar__user-usage" aria-label="我的模型额度">
              <p><span>总额度</span><strong>{usage.tokenQuota.toLocaleString('zh-CN')}</strong></p>
              <p><span>已用</span><strong>{usage.used.toLocaleString('zh-CN')}</strong></p>
              <p><span>剩余</span><strong>{usage.remaining.toLocaleString('zh-CN')}</strong></p>
            </div>
          ) : null}
          <div className="titlebar__user-section">
            <p>工作台</p>
            <button type="button" className="titlebar__user-item" onClick={openSettings}>
              <span className="titlebar__user-item-icon" aria-hidden="true"><Settings size={15} strokeWidth={1.8} /></span>
              <span>
                <strong>设置</strong>
                <small>模型、权限和工作台偏好</small>
              </span>
            </button>
            {showAdminEntry(user) ? (
              <button type="button" className="titlebar__user-item" onClick={openAdmin}>
                <span className="titlebar__user-item-icon" aria-hidden="true"><Users size={15} strokeWidth={1.8} /></span>
                <span>
                  <strong>用户管理</strong>
                  <small>查看在线状态，停用或恢复账号</small>
                </span>
              </button>
            ) : null}
          </div>
          <div className="titlebar__user-section">
            <p>账号</p>
            {changingPassword ? (
              <form
                className="titlebar__user-password"
                onSubmit={(event) => {
                  event.preventDefault();
                  void changePassword().catch((err: unknown) => {
                    setMessage(err instanceof Error ? err.message : '修改密码失败');
                  });
                }}
              >
                <label>
                  验证码
                  <input
                    aria-label="验证码"
                    placeholder="6 位验证码"
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                  />
                </label>
                <label>
                  新密码
                  <input
                    aria-label="新密码"
                    type="password"
                    placeholder="至少 8 位"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                </label>
                {message ? <p>{message}</p> : <p>先发送验证码，填入后再确认。修改成功后需要重新登录。</p>}
                <div className="titlebar__user-password-actions">
                  <button type="button" onClick={() => setChangingPassword(false)}>取消</button>
                  <button type="submit">{code.trim() ? '确认修改' : '发送验证码'}</button>
                </div>
              </form>
            ) : (
              <button type="button" className="titlebar__user-item" onClick={() => setChangingPassword(true)}>
                <span className="titlebar__user-item-icon" aria-hidden="true"><KeyRound size={15} strokeWidth={1.8} /></span>
                <span>
                  <strong>修改密码</strong>
                  <small>通过邮箱验证码更新登录密码</small>
                </span>
              </button>
            )}
            <button type="button" className="titlebar__user-item titlebar__user-item--danger" onClick={() => void signOut()}>
              <span className="titlebar__user-item-icon" aria-hidden="true"><LogOut size={15} strokeWidth={1.8} /></span>
              <span>
                <strong>退出登录</strong>
                <small>结束当前 {clientLabel} 会话</small>
              </span>
            </button>
          </div>
        </div>
      ), document.body) : null}
    </div>
  );
}

function DesktopCloudEntry({ className }: { className: string }) {
  const cloudAccount = useAuthStore((state) => state.cloudAccount);
  const setSession = useAuthStore((state) => state.setSession);
  const setView = useAppStore((state) => state.setView);
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState(cloudAccount.email);
  const [password, setPassword] = useState('');
  const [baseUrl, setBaseUrl] = useState(cloudAccount.baseUrl);
  const [advanced, setAdvanced] = useState(false);
  const [message, setMessage] = useState('');

  const login = async () => {
    setMessage('');
    const logged = await submitAuth('/api/auth/desktop/session', {
      baseUrl: baseUrl.trim(),
      email: email.trim(),
      password,
    }) as { connected?: boolean; email?: string; status?: string; baseUrl?: string };
    setSession('desktop', null, Boolean(logged.connected), {
      cloudAccount: {
        connected: Boolean(logged.connected),
        email: logged.email || email.trim(),
        status: logged.status || 'active',
        baseUrl: logged.baseUrl || baseUrl.trim(),
      },
    });
    setOpen(false);
  };

  const signOutCloud = async () => {
    await fetch('/api/auth/desktop/session', { method: 'DELETE', credentials: 'include' });
    setSession('desktop', null, false, { cloudAccount: EMPTY_CLOUD_ACCOUNT });
    setOpen(false);
  };

  const label = cloudAccount.status === 'expired'
    ? '云端登录已失效，请重新登录'
    : (cloudAccount.connected ? cloudAccount.email : '登录引力云端（可选）');

  return (
    <div className="flex items-center gap-1">
      <button type="button" className={className} onClick={() => setOpen((value) => !value)}>
        {label}
      </button>
      <button type="button" className={className} aria-label="进入工作台设置" onClick={() => setView('settings')}>
        <Settings size={17} />
      </button>
      {open ? (
        <form
          className="absolute right-3 top-12 z-20 w-72 space-y-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-3 shadow-lg"
          onSubmit={(event) => {
            event.preventDefault();
            if (cloudAccount.connected) void signOutCloud();
            else void login().catch((error: unknown) => setMessage(error instanceof Error ? error.message : '登录失败'));
          }}
        >
          <p className="text-xs text-[var(--color-text-secondary)]">登录只用于使用云端模型，本地数据不会上传</p>
          {cloudAccount.status === 'expired' ? <p className="text-xs text-[var(--color-error)]">云端登录已失效，请重新登录</p> : null}
          {message ? <p className="text-xs text-[var(--color-error)]">{message}</p> : null}
          {cloudAccount.connected ? (
            <button type="submit">退出</button>
          ) : (
            <>
              <input aria-label="邮箱" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@example.com" />
              <input aria-label="密码" type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="至少 8 位" />
              <button type="button" onClick={() => setAdvanced((value) => !value)}>高级</button>
              {advanced ? (
                <input aria-label="云端地址" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="http://127.0.0.1:8876" />
              ) : null}
              <button type="submit">登录</button>
            </>
          )}
        </form>
      ) : null}
    </div>
  );
}
