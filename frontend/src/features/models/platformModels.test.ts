import { describe, expect, it } from 'vitest';
import { mergeSelectableModels, selectorTriggerLabel } from './platformModels';
import type { SelectableModel } from './ModelSelector';

const localModel: SelectableModel = {
  id: 'deepseek-v4-flash',
  label: 'DeepSeek',
  adapter: 'deepseek',
  providerName: '本机',
};

const platformModel: SelectableModel = {
  id: 'platform:deepseek-v4-flash',
  label: '平台 DeepSeek',
  adapter: 'deepseek',
  providerName: '平台',
};

describe('platform model choices', () => {
  it('keeps only local models until login', () => {
    expect(mergeSelectableModels([localModel], [platformModel], false)).toEqual([localModel]);
  });

  it('adds the platform group after login and removes it on logout without touching local entries', () => {
    const loggedIn = mergeSelectableModels([localModel], [platformModel], true);
    expect(loggedIn.map((item) => item.id)).toEqual(['deepseek-v4-flash', 'platform:deepseek-v4-flash']);
    const loggedOut = mergeSelectableModels([localModel], [platformModel], false);
    expect(loggedOut).toEqual([localModel]);
    expect(localModel.label).toBe('DeepSeek');
  });

  it('keeps a visible platform choice when the local catalog fails', () => {
    expect(selectorTriggerLabel({
      error: 'permissionReview.maxOutputTokens',
      loading: false,
      selectedLabel: 'GPT-6',
      choiceCount: 26,
    })).toBe('GPT-6');
    expect(selectorTriggerLabel({
      error: 'permissionReview.maxOutputTokens',
      loading: false,
      choiceCount: 0,
    })).toBe('模型配置异常');
  });

  it('uses only platform models when the hosted catalog comes from key2api', () => {
    const hosted = mergeSelectableModels([localModel], [platformModel], true, true);
    expect(hosted.map((item) => item.id)).toEqual(['platform:deepseek-v4-flash']);
  });
});