/** 把本机模型与平台模型合成选择器列表。不读写 models.json。 */

import type { SelectableModel } from './ModelSelector';

export function selectorTriggerLabel(options: {
  error?: string;
  loading?: boolean;
  selectedLabel?: string;
  choiceCount: number;
}): string {
  if (options.choiceCount > 0) return options.selectedLabel || '选择模型';
  if (options.error) return '模型配置异常';
  if (options.loading) return '加载中…';
  return '选择模型';
}

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
