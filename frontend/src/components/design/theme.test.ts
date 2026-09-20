/** 暗色主题主色应对齐应用重点蓝，而不是 Carbon 浅灰白。 */

import { describe, expect, it } from 'vitest';
import { carbonDarkTheme, carbonTheme } from './theme';

describe('carbonDarkTheme', () => {
  it('主色使用重点蓝，而不是 Carbon 默认浅灰', () => {
    expect(carbonDarkTheme.theme?.token?.colorPrimary).toBe('#339cff');
    expect(carbonDarkTheme.theme?.token?.colorPrimary).not.toBe('#E4E4E4');
    expect(carbonDarkTheme.theme?.components?.Button?.primaryColor).toBe('#ffffff');
  });

  it('默认按钮使用暗底而不是浅底', () => {
    expect(carbonDarkTheme.theme?.components?.Button?.defaultBg).toBe('#222222');
    expect(carbonDarkTheme.theme?.components?.Button?.defaultBorderColor).toBe('#4a4a4a');
  });
});

describe('carbonTheme', () => {
  it('亮色主色也使用重点蓝，而不是 Carbon 近黑', () => {
    expect(carbonTheme.theme?.token?.colorPrimary).toBe('#339cff');
    expect(carbonTheme.theme?.components?.Button?.primaryColor).toBe('#ffffff');
    expect(carbonTheme.theme?.components?.Button?.defaultColor).toBe('#5f6368');
  });
});
