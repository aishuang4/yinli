/** 运行时产品功能档。启动后从后端拉取，缺省与仓库 JSON 一致。 */

import { create } from 'zustand';
import {
  DEFAULT_PRODUCT_FEATURES,
  fetchProductFeatures,
  type ProductFeatures,
} from './productFeatures';

interface ProductFeaturesState {
  features: ProductFeatures;
  loaded: boolean;
  refresh: () => Promise<void>;
}

export const useProductFeaturesStore = create<ProductFeaturesState>((set) => ({
  features: DEFAULT_PRODUCT_FEATURES,
  loaded: false,
  refresh: async () => {
    try {
      const features = await fetchProductFeatures();
      set({ features, loaded: true });
    } catch {
      set({ loaded: true });
    }
  },
}));
