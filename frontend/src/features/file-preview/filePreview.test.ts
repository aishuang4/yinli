/** 产物预览路径与 CSV 解析。 */

import { describe, expect, it } from 'vitest';
import {
  artifactContentUrl,
  artifactDownloadUrl,
  canUseSystemPreview,
  downloadableArtifactPath,
  fitDocxPreviewScale,
  isCanvasPreviewKind,
  isDesktopFilePreviewEnabled,
  parseCsvPreview,
  readPreviewHostRect,
} from './filePreview';

describe('downloadableArtifactPath', () => {
  it('抽出会话目录之后的相对路径', () => {
    expect(downloadableArtifactPath(
      '/tmp/project/.agent-workstation/sessions/sess-1/out/note.md',
      'sess-1',
    )).toBe('.agent-workstation/sessions/sess-1/out/note.md');
  });

  it('拒绝会话外的绝对路径', () => {
    expect(downloadableArtifactPath('/etc/passwd', 'sess-1')).toBe('');
  });
});

describe('artifact urls', () => {
  it('为预览和下载生成受控查询', () => {
    const path = '.agent-workstation/sessions/s1/a.md';
    expect(artifactDownloadUrl('s1', path)).toContain('files/download');
    expect(artifactContentUrl('s1', path)).toContain('files/content');
  });
});

describe('isCanvasPreviewKind', () => {
  it('Word / PDF / 图片走版式画布', () => {
    expect(isCanvasPreviewKind('docx')).toBe(true);
    expect(isCanvasPreviewKind('pdf')).toBe(true);
    expect(isCanvasPreviewKind('markdown')).toBe(false);
  });
});

describe('isDesktopFilePreviewEnabled', () => {
  it('只允许 macOS 与 Windows 桌面端预览', () => {
    expect(isDesktopFilePreviewEnabled('darwin')).toBe(true);
    expect(isDesktopFilePreviewEnabled('win32')).toBe(true);
    expect(isDesktopFilePreviewEnabled('linux')).toBe(false);
    expect(isDesktopFilePreviewEnabled(undefined)).toBe(false);
  });
});

describe('canUseSystemPreview', () => {
  it('Office / PDF / 图片走系统预览，文本类留在栏内', () => {
    expect(canUseSystemPreview('docx')).toBe(true);
    expect(canUseSystemPreview('xlsx')).toBe(true);
    expect(canUseSystemPreview('pdf')).toBe(true);
    expect(canUseSystemPreview('image')).toBe(true);
    expect(canUseSystemPreview('markdown')).toBe(false);
    expect(canUseSystemPreview('csv')).toBe(false);
  });
});

describe('fitDocxPreviewScale', () => {
  it('栏宽不足时按宽度缩放，不放大', () => {
    expect(fitDocxPreviewScale(400, 800)).toBe(0.5);
    expect(fitDocxPreviewScale(900, 800)).toBe(1);
  });
});

describe('readPreviewHostRect', () => {
  it('忽略过小的宿主区域', () => {
    expect(readPreviewHostRect({
      getBoundingClientRect: () => ({ x: 0, y: 0, width: 2, height: 2 }),
    } as HTMLElement)).toBeNull();
  });
});

describe('parseCsvPreview', () => {
  it('拆逗号并保留引号内逗号', () => {
    expect(parseCsvPreview('姓名,城市\n"李,四",武汉')).toEqual([
      ['姓名', '城市'],
      ['李,四', '武汉'],
    ]);
  });
});
