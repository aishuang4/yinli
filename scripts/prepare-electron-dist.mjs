import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const electronDist = resolve(import.meta.dirname, '../frontend/electron-dist');

await mkdir(electronDist, { recursive: true });
await writeFile(
  resolve(electronDist, 'package.json'),
  `${JSON.stringify({ type: 'commonjs' }, null, 2)}\n`,
);
