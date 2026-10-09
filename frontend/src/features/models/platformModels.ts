/** 把本机模型与平台模型合成选择器列表。不读写 models.json。 */

import type { SelectableModel } from './ModelSelector';

export function mergeSelectableModels(
  localModels: SelectableModel[],
  platformModels: SelectableModel[],
  loggedIn: boolean,
  platformOnly = false,
): SelectableModel[] {
  if (!loggedIn) return localModels;
  const provided = platformModels.filter((item) => item.id.startsWith('platform:'));
  if (platformOnly) return provided;
  const seen = new Set(localModels.map((item) => item.id));
  return [
    ...localModels,
    ...platformModels.filter((item) => item.id.startsWith('platform:') && !seen.has(item.id)),
  ];
}
