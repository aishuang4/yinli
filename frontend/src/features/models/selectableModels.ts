/** 当前目录中的可选择执行模型。 */
import { modelAvailable, type ModelCatalog } from './useModelCatalog';

export type SelectableModel = {
  id: string;
  label: string;
  adapter: string;
  providerName: string;
};

export function listSelectableModels(catalog: ModelCatalog | null): SelectableModel[] {
  if (!catalog) return [];
  const items: SelectableModel[] = [];
  for (const provider of catalog.providers) {
    for (const model of catalog.models) {
      if (model.providerId !== provider.id || !modelAvailable(catalog, model)) continue;
      items.push({
        id: model.id,
        label: model.label || model.model,
        adapter: provider.adapter,
        providerName: provider.name,
      });
    }
  }
  return items;
}

