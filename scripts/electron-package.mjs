/**
 * Electron 桌面端跨平台打包入口。
 *
 * Windows 原生 npm 无法直接执行 shell 脚本，因此在 Windows 上使用 Node
 * 编排构建命令；macOS/Linux 继续复用现有 shell 脚本及 macOS 产物逻辑。
 */

import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const rootDir = resolve(import.meta.dirname, '..');
const backendDir = resolve(rootDir, 'backend');
const frontendDir = resolve(rootDir, 'frontend');
const target = process.argv[2] ?? '';

function run(command, args, cwd, env = process.env) {
  const result = spawnSync(command, args, {
    cwd,
    env,
    stdio: 'inherit',
    windowsHide: true,
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

if (process.platform !== 'win32') {
  run('bash', [resolve(rootDir, 'scripts/electron-package.sh'), target].filter(Boolean), rootDir);
  process.exit(0);
}

if (target !== '' && target !== 'win') {
  console.error(`Windows 打包入口不支持目标：${target}`);
  process.exit(1);
}

const uvCommand = 'uv.exe';
const backendPython = resolve(backendDir, '.venv/Scripts/python.exe');
const npmCli = process.env.npm_execpath;
const buildEnv = {
  ...process.env,
  UV_CACHE_DIR: process.env.UV_CACHE_DIR || resolve(rootDir, '.uv-cache-local'),
};

if (!npmCli || !existsSync(npmCli)) {
  console.error('无法定位 npm CLI，请通过 npm run electron:build:win 启动打包。');
  process.exit(1);
}

// 优先复用项目已有解释器，避免损坏的用户级 uv managed Python 阻断构建。
if (!buildEnv.UV_PYTHON && existsSync(backendPython)) {
  buildEnv.UV_PYTHON = backendPython;
}

console.log('构建桌面端后端...');
run(uvCommand, ['sync'], backendDir, buildEnv);
run(
  uvCommand,
  ['run', '--with', 'pyinstaller', 'pyinstaller', '--clean', '--noconfirm', 'backend-server.spec'],
  backendDir,
  buildEnv,
);

console.log('构建前端...');
run(process.execPath, [npmCli, 'run', 'build'], frontendDir, buildEnv);

console.log('编译 Electron 主进程...');
run(process.execPath, [npmCli, 'run', 'electron:compile'], frontendDir, buildEnv);

console.log('打包 Windows 桌面端...');
run(
  process.execPath,
  [
    npmCli,
    'exec',
    '--',
    'electron-builder',
    '--projectDir',
    'desktop-package',
    '--config',
    '../electron-builder.yml',
    '--win',
  ],
  frontendDir,
  buildEnv,
);
