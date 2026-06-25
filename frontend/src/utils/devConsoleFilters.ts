/**
 * 开发期控制台降噪。
 *
 * 这些告警来自 Ant Design / Ant Design X / AgentScope Design 的依赖内部，
 * 当前业务代码无法直接修复。只在 DEV 环境按完整特征过滤，避免吞掉真实错误。
 */

const KNOWN_THIRD_PARTY_WARNING_PARTS = [
  'forwardRef render functions accept exactly two parameters',
  'The pseudo class ":first-child" is potentially unsafe',
  'Warning: [antd: Tooltip] `overlayClassName` is deprecated',
];

let installed = false;

export function installDevConsoleFilters() {
  if (installed || !import.meta.env.DEV) {
    return;
  }
  installed = true;

  const originalError = console.error.bind(console);
  const originalWarn = console.warn.bind(console);

  const shouldIgnore = (args: unknown[]) => (
    args.some((arg) => (
      typeof arg === 'string' &&
      KNOWN_THIRD_PARTY_WARNING_PARTS.some((part) => arg.includes(part))
    ))
  );

  console.error = (...args: unknown[]) => {
    if (shouldIgnore(args)) {
      return;
    }
    originalError(...args);
  };

  console.warn = (...args: unknown[]) => {
    if (shouldIgnore(args)) {
      return;
    }
    originalWarn(...args);
  };
}

installDevConsoleFilters();
