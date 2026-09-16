import { describe, expect, it } from 'vitest';
import {
  DEFAULT_PRODUCT_FEATURES,
  isViewEnabled,
  normalizeProductFeatures,
} from './productFeatures';

describe('productFeatures', () => {
  it('forces chat and settings on even if the file turns them off', () => {
    const features = normalizeProductFeatures({
      views: { chat: false, settings: false, runs: true },
    });
    expect(features.views.chat).toBe(true);
    expect(features.views.settings).toBe(true);
    expect(features.views.runs).toBe(true);
    expect(isViewEnabled(features, 'chat')).toBe(true);
  });

  it('fills missing keys from the shipping defaults', () => {
    const features = normalizeProductFeatures({});
    expect(features).toEqual(DEFAULT_PRODUCT_FEATURES);
    expect(isViewEnabled(features, 'runs')).toBe(false);
  });
});
