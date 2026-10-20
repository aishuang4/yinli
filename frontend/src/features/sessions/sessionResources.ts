/** 全局 catalog 與 Session grant 的對照列。 */

export interface CatalogResource {
  resourceType: string;
  resourceId: string;
  label: string;
}

export interface ResourceGrantRow {
  resourceType: string;
  resourceId: string;
  enabled: boolean;
}

export interface ResourceChoice {
  resourceType: string;
  resourceId: string;
  label: string;
  granted: boolean;
  reason: string;
}

export function mergeResourceChoices(
  catalog: CatalogResource[],
  grants: ResourceGrantRow[],
  rejection?: { resourceType: string; resourceId: string; reason: string },
): ResourceChoice[] {
  const granted = new Map(grants.map((item) => [`${item.resourceType}:${item.resourceId}`, item.enabled]));
  return catalog.map((item) => {
    const key = `${item.resourceType}:${item.resourceId}`;
    const rejected = rejection && rejection.resourceType === item.resourceType && rejection.resourceId === item.resourceId;
    return {
      resourceType: item.resourceType,
      resourceId: item.resourceId,
      label: item.label || item.resourceId,
      granted: granted.get(key) === true,
      reason: rejected ? rejection.reason : '',
    };
  });
}
