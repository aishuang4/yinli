import { readFile, writeFile } from 'node:fs/promises';

/** 将多尺寸 PNG 封装成标准 ICNS，避免依赖不同 macOS 版本的 iconutil。 */

const [outputPath, ...entries] = process.argv.slice(2);
if (!outputPath || entries.length === 0) {
  throw new Error('用法：node create-icns.mjs <output.icns> <type=png-path>...');
}

const chunks = [];
for (const entry of entries) {
  const separator = entry.indexOf('=');
  const type = entry.slice(0, separator);
  const filePath = entry.slice(separator + 1);
  if (separator !== 4 || !filePath) {
    throw new Error(`无效 ICNS 条目：${entry}`);
  }

  const png = await readFile(filePath);
  const chunk = Buffer.allocUnsafe(8 + png.length);
  chunk.write(type, 0, 4, 'ascii');
  chunk.writeUInt32BE(chunk.length, 4);
  png.copy(chunk, 8);
  chunks.push(chunk);
}

const totalLength = 8 + chunks.reduce((total, chunk) => total + chunk.length, 0);
const header = Buffer.allocUnsafe(8);
header.write('icns', 0, 4, 'ascii');
header.writeUInt32BE(totalLength, 4);
await writeFile(outputPath, Buffer.concat([header, ...chunks], totalLength));
