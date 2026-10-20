/** 托管模式的注册、验证、登录和重置密码。 */

import { Button } from '@/components/design';
import { ArrowLeft, ArrowRight, Moon, Sun } from 'lucide-react';
import { useSettingsStore } from '../../stores/settingsStore';
import { FormError, PasswordField, TextField } from '../../components/form';
import { AuthBrandPanel } from './AuthBrandPanel';
import './auth.css';

export type AuthFormMode = 'login' | 'register' | 'verify' | 'reset';

const COPY: Record<AuthFormMode, { title: string; lead: string; action: string }> = {
  login: {
    title: '欢迎回到引力',
    lead: '登录你的账号，继续创造下一份成果。',
    action: '登录',
  },
  register: {
    title: '从这里，开始创造',
    lead: '创建引力账号，开启你的 AI 办公体验。',
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
  onBack,
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
  onBack?: () => void;
}) {
  const copy = COPY[mode];
  const theme = useSettingsStore((state) => state.theme);
  const toggleTheme = useSettingsStore((state) => state.toggleTheme);
  const themeAction = theme === 'dark' ? '切换亮色主题' : '切换暗色主题';
  return (
    <main className="auth-screen">
      <AuthBrandPanel />
      <section className="auth-screen__panel" aria-labelledby="auth-title">
        <div className="auth-screen__topline">
          <span>引力 YINLI <span className="auth-screen__topline-divider">/</span> 账号</span>
          <div className="auth-screen__top-actions">
            {onBack && mode === 'login' ? (
              <button type="button" onClick={onBack}><ArrowLeft size={14} /> 返回工作台</button>
            ) : null}
            {mode !== 'login' ? (
              <button type="button" onClick={() => onMode('login')}><ArrowLeft size={14} /> 返回登录</button>
            ) : null}
            <Button
              className="auth-screen__theme-toggle"
              type="text"
              shape="circle"
              htmlType="button"
              icon={(
                <span className="auth-screen__theme-icons" aria-hidden="true">
                  <Sun className="auth-screen__theme-sun" size={19} />
                  <Moon className="auth-screen__theme-moon" size={19} />
                </span>
              )}
              aria-label={themeAction}
              title={themeAction}
              onClick={toggleTheme}
            />
          </div>
        </div>
        <div className="auth-screen__card">
          <header className="auth-screen__header">
            <p className="auth-screen__product"><span /> 你的下一份成果，从这里开始</p>
            <h1 id="auth-title">{copy.title}</h1>
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
                aria-label="托管服务地址"
                value={serviceUrl || ''}
                placeholder="https://example.com"
                onChange={(event) => onServiceUrl?.(event.target.value)}
              />
            ) : null}
            <TextField
              label="邮箱"
              aria-label="邮箱"
              required
              type="email"
              autoComplete="email"
              placeholder="name@example.com"
              value={email}
              onChange={(event) => onEmail(event.target.value)}
            />
            {mode !== 'verify' ? (
              <PasswordField
                label={<span className="auth-screen__password-label"><span>{mode === 'reset' ? '新密码' : '密码'}<span className="auth-screen__password-required" aria-hidden="true">*</span></span>{mode === 'login' ? <button type="button" onClick={() => onMode('reset')}>忘记密码？</button> : null}</span>}
                aria-label={mode === 'reset' ? '新密码' : '密码'}
                aria-required
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                placeholder="至少 8 位"
                value={password}
                onChange={(event) => onPassword(event.target.value)}
              />
            ) : null}
            {mode === 'verify' || mode === 'reset' ? (
              <TextField
                label="验证码"
                aria-label="验证码"
                required={mode === 'verify'}
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="6 位数字"
                value={code}
                onChange={(event) => onCode(event.target.value)}
              />
            ) : null}
            <div aria-live="polite" className="auth-screen__feedback"><FormError>{error}</FormError></div>
            <Button className="auth-screen__submit" type="primary" htmlType="submit" block loading={submitting}>
              <span>{mode === 'reset' && !code ? '发送验证码' : copy.action}</span>{!submitting ? <ArrowRight size={17} /> : null}
            </Button>
            <div className="auth-screen__links">
              {mode === 'login' ? (
                <>
                  <span>第一次使用引力？</span>
                  <button type="button" onClick={() => onMode('register')}>创建账号</button>
                </>
              ) : (
                <button type="button" onClick={() => onMode('login')}>返回登录</button>
              )}
            </div>
          </form>
        </div>
        <footer className="auth-screen__footer">专注你的想法，余下的交给引力。</footer>
      </section>
    </main>
  );
}
