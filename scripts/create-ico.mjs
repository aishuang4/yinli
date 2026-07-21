import { readFile, writeFile } from 'node:fs/promises';

/** 将多尺寸 PNG 封装成 Windows ICO，避免打包时下载额外转换工具。 */

const [outputPath, ...entries] = process.argv.slice(2);
if (!outputPath || entries.length === 0) {
  throw new Error('用法：node create-ico.mjs <output.ico> <size=png-path>...');
}

const images = [];
for (const entry of entries) {
  const separator = entry.indexOf('=');
  const size = Number(entry.slice(0, separator));
  const filePath = entry.slice(separator + 1);
  if (!Number.isInteger(size) || size < 1 || size > 256 || !filePath) {
    throw new Error(`无效 ICO 条目：${entry}`);
  }
  images.push({ size, png: await readFile(filePath) });
}

const headerLength = 6 + images.length * 16;
const header = Buffer.alloc(headerLength);
header.writeUInt16LE(0, 0);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(images.length, 4);

let imageOffset = headerLength;
images.forEach(({ size, png }, index) => {
  const entryOffset = 6 + index * 16;
  header.writeUInt8(size === 256 ? 0 : size, entryOffset);
  header.writeUInt8(size === 256 ? 0 : size, entryOffset + 1);
  header.writeUInt8(0, entryOffset + 2);
  header.writeUInt8(0, entryOffset + 3);
  header.writeUInt16LE(1, entryOffset + 4);
  header.writeUInt16LE(32, entryOffset + 6);
  header.writeUInt32LE(png.length, entryOffset + 8);
  header.writeUInt32LE(imageOffset, entryOffset + 12);
  imageOffset += png.length;
});

await writeFile(outputPath, Buffer.concat([header, ...images.map(({ png }) => png)], imageOffset));
