/**
 * 桌面端 API 地址适配。
 *
 * 浏览器开发模式继续使用 Vite 的 `/api` 代理；Electron 生产模式从
 * `file://` 加载页面，需要把同样的相对请求转发到本机内置后端。
 */

const DESKTOP_API_ORIGIN = 'http://127.0.0.1:8765';

export function resolveApiUrl(value: string): string {
  if (!window.electronAPI || !value.startsWith('/api')) {
    return value;
  }
  return `${DESKTOP_API_ORIGIN}${value}`;
}

let desktopFetchBridgeInstalled = false;

export function installDesktopFetchBridge(): void {
  if (!window.electronAPI || desktopFetchBridgeInstalled) {
    return;
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    if (typeof input === 'string') {
      return nativeFetch(resolveApiUrl(input), init);
    }
    if (input instanceof URL) {
      return nativeFetch(new URL(resolveApiUrl(input.toString())), init);
    }
    if (input instanceof Request && input.url.startsWith('file:///api')) {
      const apiPath = input.url.slice('file://'.length);
      return nativeFetch(new Request(resolveApiUrl(apiPath), input), init);
    }
    return nativeFetch(input, init);
  }) as typeof window.fetch;

  desktopFetchBridgeInstalled = true;
}
