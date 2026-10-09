/** 按服务模式决定要不要挡住工作台，并维护管理员页所需的登录状态。 */

import { useEffect, useState, type ReactNode } from 'react';
import { AuthScreen, type AuthFormMode } from './AuthScreen';
import { fetchAuthConfig, fetchCurrentUser, submitAuth } from './authClient';
import { workbenchGate, type AuthUser } from './authRules';
import { useAuthStore } from './authStore';

export function HostedSessionGate({ children }: { children: ReactNode }) {
  const serveMode = useAuthStore((state) => state.serveMode);
  const user = useAuthStore((state) => state.user);
  const loaded = useAuthStore((state) => state.loaded);
  const setSession = useAuthStore((state) => state.setSession);
  const [mode, setMode] = useState<AuthFormMode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [serviceUrl] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const nextMode = await fetchAuthConfig();
        const nextUser = nextMode === 'hosted' ? await fetchCurrentUser() : null;
        let desktopConnected = false;
        if (nextMode === 'desktop') {
          const response = await fetch('/api/auth/desktop/session', { credentials: 'include' });
          if (response.ok) {
            const payload = await response.json();
            desktopConnected = Boolean(payload.connected);
          }
        }
        if (!cancelled) setSession(nextMode, nextUser, desktopConnected);
      } catch {
        if (!cancelled) setSession('desktop', null, false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setSession]);

  const refreshUser = async () => {
    const nextUser = await fetchCurrentUser();
    setSession(serveMode, nextUser, useAuthStore.getState().desktopConnected);
  };

  const onSubmit = async () => {
    setError('');
    setSubmitting(true);
    try {
      if (mode === 'register') {
        await submitAuth('/api/auth/register', { email, password });
        setMode('verify');
        return;
      }
      if (mode === 'verify') {
        await submitAuth('/api/auth/verify', { email, code });
        setMode('login');
        return;
      }
      if (mode === 'reset') {
        if (!code) {
          await submitAuth('/api/auth/forgot-password', { email });
          setError('验证码已发送');
          return;
        }
        await submitAuth('/api/auth/reset-password', { email, code, password });
        setMode('login');
        return;
      }
      if (window.electronAPI && serviceUrl.trim()) {
        await submitAuth('/api/auth/desktop/session', { baseUrl: serviceUrl.trim(), email, password });
        setSession('desktop', null, true);
        return;
      }
      const loggedIn = await submitAuth('/api/auth/login', { email, password, clientKind: 'web' }) as { user?: AuthUser };
      if (loggedIn.user) setSession(serveMode, loggedIn.user, false);
      await refreshUser();
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (!loaded) return null;
  if (workbenchGate(serveMode, user) === 'login') {
    return (
      <AuthScreen
        mode={mode}
        email={email}
        password={password}
        code={code}
        error={error}
        submitting={submitting}
        onEmail={setEmail}
        onPassword={setPassword}
        onCode={setCode}
        onMode={setMode}
        onSubmit={() => void onSubmit()}
      />
    );
  }
  return children;
}
