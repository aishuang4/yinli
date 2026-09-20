/**
 * 为当前 Electron 版本编译桌面端内嵌系统预览原生模块。
 * macOS 编译 QLPreviewView，Windows 编译 IPreviewHandler；Linux 跳过。
 */

import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const require = createRequire(import.meta.url);
const rootDir = resolve(import.meta.dirname, '..');
const nativeDir = resolve(rootDir, 'electron/native');
const outDir = resolve(rootDir, 'frontend/electron-dist/native');
const frontendDir = resolve(rootDir, 'frontend');

if (process.platform !== 'darwin' && process.platform !== 'win32') {
  process.exit(0);
}

const electronPackage = require(resolve(frontendDir, 'node_modules/electron/package.json'));
const electronVersion = String(electronPackage.version || '').replace(/^v/, '');
if (!electronVersion) {
  console.warn('[system-preview] 找不到 Electron 版本，跳过原生预览模块');
  process.exit(0);
}

const nodeGyp = resolve(frontendDir, 'node_modules/.bin/node-gyp');
const result = spawnSync(
  existsSync(nodeGyp) ? nodeGyp : 'npx',
  [
    ...(existsSync(nodeGyp) ? [] : ['--yes', 'node-gyp']),
    'rebuild',
    `--target=${electronVersion}`,
    `--arch=${process.arch}`,
    '--dist-url=https://electronjs.org/headers',
  ],
  {
    cwd: nativeDir,
    stdio: 'inherit',
    env: {
      ...process.env,
      npm_config_runtime: 'electron',
      npm_config_target: electronVersion,
      npm_config_arch: process.arch,
      npm_config_disturl: 'https://electronjs.org/headers',
      npm_config_build_from_source: 'true',
    },
  },
);

if (result.status !== 0) {
  console.warn('[system-preview] 原生预览模块编译失败，将回退到栏内预览或不预览');
  process.exit(0);
}

const built = resolve(nativeDir, 'build/Release/system_preview.node');
if (!existsSync(built)) {
  console.warn('[system-preview] 未找到编译产物');
  process.exit(0);
}

mkdirSync(outDir, { recursive: true });
copyFileSync(built, resolve(outDir, 'system_preview.node'));
console.log(`[system-preview] 已编译 ${process.platform} 内嵌系统预览模块`);
