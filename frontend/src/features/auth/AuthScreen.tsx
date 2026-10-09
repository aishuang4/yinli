/** 托管模式的注册、验证、登录和重置密码。 */

import { Button } from '@/components/design';
import { FormError, PasswordField, TextField } from '../../components/form';

export type AuthFormMode = 'login' | 'register' | 'verify' | 'reset';

const COPY: Record<AuthFormMode, { title: string; lead: string; action: string }> = {
  login: {
    title: '登录',
    lead: '使用邮箱进入引力 YINLI。',
    action: '登录',
  },
  register: {
    title: '注册',
    lead: '注册后需要验证邮箱。验证码会发到邮箱，开发环境可在后端日志里查看。',
    action: '创建账号',
  },
  verify: {
    title: '验证邮箱',
    lead: '输入刚收到的 6 位验证码。通过后即可登录。',
    action: '完成验证',
  },
  reset: {
    title: '重置密码',
    lead: '先发送验证码。填入验证码和新密码后即可重设。',
    action: '重置密码',
  },
};

export function AuthScreen({
  mode,
  email,
  password,
  code,
  error,
  serviceUrl,
  showServiceUrl,
  submitting = false,
  onEmail,
  onPassword,
  onCode,
  onServiceUrl,
  onMode,
  onSubmit,
}: {
  mode: AuthFormMode;
  email: string;
  password: string;
  code: string;
  error: string;
  serviceUrl?: string;
  showServiceUrl?: boolean;
  submitting?: boolean;
  onEmail: (value: string) => void;
  onPassword: (value: string) => void;
  onCode: (value: string) => void;
  onServiceUrl?: (value: string) => void;
  onMode: (mode: AuthFormMode) => void;
  onSubmit: () => void;
}) {
  const copy = COPY[mode];
  return (
    <main className="auth-screen">
      <section className="auth-screen__card">
        <header className="auth-screen__header">
          <p className="auth-screen__product">引力 YINLI</p>
          <h1>{copy.title}</h1>
          <p className="auth-screen__lead">{copy.lead}</p>
        </header>
        <form
          className="auth-screen__form"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          {showServiceUrl ? (
            <TextField
              label="托管服务地址"
              value={serviceUrl || ''}
              placeholder="https://example.com"
              onChange={(event) => onServiceUrl?.(event.target.value)}
            />
          ) : null}
          <TextField
            label="邮箱"
            required
            type="email"
            autoComplete="email"
            placeholder="name@example.com"
            value={email}
            onChange={(event) => onEmail(event.target.value)}
          />
          {mode !== 'verify' ? (
            <PasswordField
              label="密码"
              required
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              placeholder="至少 8 位"
              value={password}
              onChange={(event) => onPassword(event.target.value)}
            />
          ) : null}
          {mode === 'verify' || mode === 'reset' ? (
            <TextField
              label="验证码"
              required={mode === 'verify'}
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="6 位数字"
              value={code}
              onChange={(event) => onCode(event.target.value)}
            />
          ) : null}
          <FormError>{error}</FormError>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            {copy.action}
          </Button>
          <div className="auth-screen__links">
            {mode === 'login' ? (
              <>
                <button type="button" onClick={() => onMode('register')}>注册账号</button>
                <button type="button" onClick={() => onMode('reset')}>忘记密码</button>
              </>
            ) : (
              <button type="button" onClick={() => onMode('login')}>返回登录</button>
            )}
          </div>
        </form>
      </section>
    </main>
  );
}
