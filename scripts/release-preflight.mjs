/**
 * 桌面 beta 发布前跨平台静态预检。
 *
 * 该检查不启动 Electron，只验证打包输入、后端资源和不会随安装包交付的敏感文件。
 */

import { existsSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const frontend = join(root, 'frontend');
const backend = join(root, 'backend');
const required = [
  join(frontend, 'dist', 'index.html'),
  join(frontend, 'electron-dist', 'main.js'),
  join(frontend, 'electron-dist', 'preload.js'),
  join(frontend, 'build', 'icon.ico'),
  join(frontend, 'build', 'installer.nsh'),
  join(backend, 'backend-server.spec'),
];

const missing = required.filter((path) => !existsSync(path));
if (missing.length > 0) {
  console.error('桌面发布预检失败，缺少以下输入：');
  for (const path of missing) console.error(`- ${path}`);
  process.exit(1);
}

const backendDist = join(backend, 'dist');
const backendExecutables = readdirSync(backendDist, { withFileTypes: true })
  .filter((entry) => entry.isFile() && (entry.name === 'backend-server' || entry.name === 'backend-server.exe'))
  .map((entry) => join(backendDist, entry.name));
const expectedBackendName = process.platform === 'win32' ? 'backend-server.exe' : 'backend-server';
const expectedBackend = join(backendDist, expectedBackendName);
if (!existsSync(expectedBackend) || statSync(expectedBackend).size < 1024 * 1024) {
  console.error('桌面发布预检失败：backend/dist 中没有有效的后端可执行文件。');
  console.error(`当前平台 ${process.platform} 需要：${expectedBackendName}`);
  process.exit(1);
}

const forbiddenNames = new Set(['.env', '.env.local', 'agent_workstation.db', 'backend-process.json']);
const forbidden = [];
function scan(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (forbiddenNames.has(entry.name)) forbidden.push(path);
    if (entry.isDirectory() && entry.name !== 'node_modules' && entry.name !== '.git') scan(path);
  }
}
scan(join(frontend, 'desktop-package'));
if (forbidden.length > 0) {
  console.error('桌面发布预检失败：desktop-package 包含不应交付的运行时文件：');
  for (const path of forbidden) console.error(`- ${path}`);
  process.exit(1);
}

console.log('桌面发布预检通过。');
console.log(`- 后端可执行文件：${backendExecutables.map((path) => path.split('/').pop()).join(', ')}`);
console.log('- 前端 dist、Electron 主进程、图标和 NSIS 脚本齐全。');
console.log('- desktop-package 未发现用户数据库、运行时进程记录或 .env。');
