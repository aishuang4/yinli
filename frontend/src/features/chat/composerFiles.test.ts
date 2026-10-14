/** 拖入和粘贴文件的分类。 */

import { describe, expect, it, vi } from 'vitest';
import { filesFromTransfer, ingestComposerFiles, isFileDrag, partitionComposerFiles } from './composerFiles';

function file(name: string, type: string): File {
  return new File(['x'], name, { type });
}

describe('composer file transfer', () => {
  it('识别文件拖拽，忽略纯文本', () => {
    expect(isFileDrag({ types: ['Files'] })).toBe(true);
    expect(isFileDrag({ types: ['text/plain'] })).toBe(false);
    expect(isFileDrag(null)).toBe(false);
  });

  it('files 为空时从剪贴板 items 取截图', () => {
    const shot = file('image.png', 'image/png');
    const files = filesFromTransfer({
      files: [],
      items: [
        { kind: 'string' },
        { kind: 'file', getAsFile: () => shot },
      ],
    });
    expect(files).toEqual([shot]);
  });

  it('图片和普通文件分开，无名图片补上文件名', () => {
    const { images, others } = partitionComposerFiles([
      file('notes.pdf', 'application/pdf'),
      new File(['x'], '', { type: 'image/png' }),
    ]);
    expect(others.map((item) => item.name)).toEqual(['notes.pdf']);
    expect(images.map((item) => item.name)).toEqual(['paste-1.png']);
  });

  it('一次交给图片入口，其余逐个交给文件入口', async () => {
    const onImageFiles = vi.fn();
    const onFile = vi.fn();
    await ingestComposerFiles(
      [file('a.png', 'image/png'), file('b.txt', 'text/plain'), file('c.png', 'image/png')],
      { onImageFiles, onFile },
    );
    expect(onImageFiles).toHaveBeenCalledTimes(1);
    expect(onImageFiles.mock.calls[0][0].map((item: File) => item.name)).toEqual(['a.png', 'c.png']);
    expect(onFile.mock.calls.map((call) => call[0].name)).toEqual(['b.txt']);
  });
});
