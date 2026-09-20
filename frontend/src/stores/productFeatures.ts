/** 产品功能档：与后端 config/product-features.json 同一套键。 */

export const PRODUCT_VIEW_KEYS = [
  'chat',
  'runs',
  'traces',
  'sandbox',
  'jobs',
  'memory',
  'agents',
  'models',
  'tools',
  'mcp',
  'skills',
  'subagents',
  'settings',
] as const;

export const PRODUCT_SURFACE_KEYS = [
  'workflow',
  'groupStudio',
  'teamMode',
  'planMode',
] as const;

export const ALWAYS_ON_VIEWS = new Set<(typeof PRODUCT_VIEW_KEYS)[number]>(['chat', 'settings']);

export type ProductViewKey = (typeof PRODUCT_VIEW_KEYS)[number];
export type ProductSurfaceKey = (typeof PRODUCT_SURFACE_KEYS)[number];

export interface ProductFeatures {
  views: Record<ProductViewKey, boolean>;
  surfaces: Record<ProductSurfaceKey, boolean>;
}

export const DEFAULT_PRODUCT_FEATURES: ProductFeatures = {
  views: {
    chat: true,
    runs: false,
    traces: true,
    sandbox: true,
    jobs: true,
    memory: true,
    agents: true,
    models: true,
    tools: true,
    mcp: true,
    skills: true,
    subagents: true,
    settings: true,
  },
  surfaces: {
    workflow: true,
    groupStudio: true,
    teamMode: true,
    planMode: true,
  },
};

function coerceFlag(value: unknown, fallback: boolean): boolean {
  if (typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'string') {
    const lowered = value.trim().toLowerCase();
    if (lowered === 'true' || lowered === '1' || lowered === 'yes' || lowered === 'on') {
      return true;
    }
    if (lowered === 'false' || lowered === '0' || lowered === 'no' || lowered === 'off') {
      return false;
    }
  }
  if (value === 0 || value === 1) {
    return Boolean(value);
  }
  return fallback;
}

export function normalizeProductFeatures(raw: unknown): ProductFeatures {
  const payload = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const rawViews = payload.views && typeof payload.views === 'object'
    ? payload.views as Record<string, unknown>
    : {};
  const rawSurfaces = payload.surfaces && typeof payload.surfaces === 'object'
    ? payload.surfaces as Record<string, unknown>
    : {};
  const views = Object.fromEntries(
    PRODUCT_VIEW_KEYS.map((key) => [
      key,
      ALWAYS_ON_VIEWS.has(key) ? true : coerceFlag(rawViews[key], DEFAULT_PRODUCT_FEATURES.views[key]),
    ]),
  ) as Record<ProductViewKey, boolean>;
  const surfaces = Object.fromEntries(
    PRODUCT_SURFACE_KEYS.map((key) => [
      key,
      coerceFlag(rawSurfaces[key], DEFAULT_PRODUCT_FEATURES.surfaces[key]),
    ]),
  ) as Record<ProductSurfaceKey, boolean>;
  return { views, surfaces };
}

export function isViewEnabled(features: ProductFeatures, view: string): boolean {
  if (ALWAYS_ON_VIEWS.has(view as ProductViewKey)) {
    return true;
  }
  return Boolean(features.views[view as ProductViewKey]);
}

export function isSurfaceEnabled(features: ProductFeatures, surface: ProductSurfaceKey): boolean {
  return Boolean(features.surfaces[surface]);
}

export async function fetchProductFeatures(): Promise<ProductFeatures> {
  const response = await fetch('/api/settings/product-features', { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`product-features ${response.status}`);
  }
  return normalizeProductFeatures(await response.json());
}
