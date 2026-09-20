/** + 菜单双栏定位：二级跟随当前一级项，而不是与一级共用 bottom。 */

import { describe, expect, it } from 'vitest';
import {
  computePlusMenuFlyoutPosition,
  computePlusMenuPrimaryPosition,
  computePlusMenuSubmenuTop,
  samePlusMenuPosition,
} from './plusMenu';

const viewport = {
  viewportWidth: 1280,
  viewportHeight: 800,
};

describe('computePlusMenuFlyoutPosition', () => {
  it('二级顶部跟随当前一级项，短面板不会贴在一级底部', () => {
    const pos = computePlusMenuFlyoutPosition({
      anchorLeft: 24,
      anchorTop: 740,
      categoryItemTop: 560,
      submenuHeight: 120,
      ...viewport,
    });
    expect(pos.submenuTop).toBe(560);
    expect(pos.primaryBottom).toBe(68);
    const sharedBottomVisualTop = viewport.viewportHeight - pos.primaryBottom - 120;
    expect(pos.submenuTop).toBeLessThan(sharedBottomVisualTop);
  });

  it('切换到更靠下的一级项时，二级跟着下移', () => {
    const files = computePlusMenuFlyoutPosition({
      anchorLeft: 24,
      anchorTop: 740,
      categoryItemTop: 560,
      submenuHeight: 120,
      ...viewport,
    });
    const skills = computePlusMenuFlyoutPosition({
      anchorLeft: 24,
      anchorTop: 740,
      categoryItemTop: 668,
      submenuHeight: 120,
      ...viewport,
    });
    expect(skills.submenuTop).toBe(668);
    expect(skills.submenuTop).toBeGreaterThan(files.submenuTop);
  });

  it('二级超出视口底部时上移，仍与一级项有垂直重叠', () => {
    const itemTop = 720;
    const pos = computePlusMenuFlyoutPosition({
      anchorLeft: 24,
      anchorTop: 760,
      categoryItemTop: itemTop,
      submenuHeight: 200,
      ...viewport,
    });
    expect(pos.submenuTop).toBe(592);
    expect(pos.submenuTop + 200).toBeLessThanOrEqual(viewport.viewportHeight - 8);
    expect(itemTop).toBeGreaterThanOrEqual(pos.submenuTop);
    expect(itemTop).toBeLessThan(pos.submenuTop + 200);
  });

  it('一级项贴近视口顶部时二级不小于内边距', () => {
    const pos = computePlusMenuFlyoutPosition({
      anchorLeft: 24,
      anchorTop: 740,
      categoryItemTop: 0,
      submenuHeight: 160,
      ...viewport,
    });
    expect(pos.submenuTop).toBe(8);
  });

  it('右侧空间不足时把二级放到一级左侧', () => {
    const pos = computePlusMenuFlyoutPosition({
      anchorLeft: 1000,
      anchorTop: 740,
      categoryItemTop: 560,
      submenuHeight: 120,
      ...viewport,
    });
    expect(pos.rightLeft).toBeLessThan(pos.left);
    expect(pos.rightLeft + 300).toBeLessThanOrEqual(pos.left);
  });
});

describe('computePlusMenuPrimaryPosition', () => {
  it('一级坐标只由发送按钮决定，不随当前一级项变化', () => {
    const pos = computePlusMenuPrimaryPosition({
      anchorLeft: 24,
      anchorTop: 740,
      ...viewport,
    });
    expect(pos.left).toBe(24);
    expect(pos.primaryBottom).toBe(68);
    expect(pos.rightLeft).toBe(218);
  });
});

describe('computePlusMenuSubmenuTop', () => {
  it('一级已摆正后，二级顶部等于当前一级项顶部', () => {
    expect(computePlusMenuSubmenuTop({
      categoryItemTop: 520,
      submenuHeight: 110,
      viewportHeight: 720,
    })).toBe(520);
  });
});

describe('samePlusMenuPosition', () => {
  it('坐标完全相同时视为未变化', () => {
    const pos = computePlusMenuFlyoutPosition({
      anchorLeft: 24,
      anchorTop: 740,
      categoryItemTop: 560,
      submenuHeight: 120,
      ...viewport,
    });
    expect(samePlusMenuPosition(pos, pos)).toBe(true);
    expect(samePlusMenuPosition(null, pos)).toBe(false);
    expect(samePlusMenuPosition(pos, { ...pos, submenuTop: pos.submenuTop + 1 })).toBe(false);
  });
});
