import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const electronDist = resolve(import.meta.dirname, '../frontend/electron-dist');
const frontendRoot = resolve(import.meta.dirname, '../frontend');
const desktopPackage = resolve(frontendRoot, 'desktop-package');
const buildResources = resolve(frontendRoot, 'build');

await mkdir(electronDist, { recursive: true });
await writeFile(
  resolve(electronDist, 'package.json'),
  `${JSON.stringify({ type: 'commonjs' }, null, 2)}\n`,
);

// 网页与桌面打包共用同一矢量源，避免 macOS、Windows 图标漂移。
await mkdir(buildResources, { recursive: true });
await cp(resolve(frontendRoot, 'public/favicon.svg'), resolve(buildResources, 'icon.svg'));
await cp(resolve(frontendRoot, 'resources/icon.ico'), resolve(buildResources, 'icon.ico'));

const frontendPackage = JSON.parse(
  await readFile(resolve(frontendRoot, 'package.json'), 'utf8'),
);
await rm(desktopPackage, { recursive: true, force: true });
await mkdir(desktopPackage, { recursive: true });
await cp(resolve(frontendRoot, 'dist'), resolve(desktopPackage, 'dist'), { recursive: true });
await cp(electronDist, resolve(desktopPackage, 'electron-dist'), { recursive: true });
await writeFile(
  resolve(desktopPackage, 'package.json'),
  `${JSON.stringify({
    name: 'agent-workstation-desktop',
    version: frontendPackage.version,
    description: frontendPackage.description,
    author: frontendPackage.author,
    private: true,
    main: 'electron-dist/main.js',
  }, null, 2)}\n`,
);
