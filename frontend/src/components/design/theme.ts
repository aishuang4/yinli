/** AgentScope carbon token 到 Ant Design ThemeConfig 的转换。 */

import carbonDarkThemeJson from '@agentscope-ai/design/lib/antd/themes/carbonDarkTheme.json';
import carbonThemeJson from '@agentscope-ai/design/lib/antd/themes/carbonTheme.json';
import generateThemeByToken from '@agentscope-ai/design/lib/antd/themes/generateThemeByToken';

/**
 * Carbon 亮色把 colorPrimary 做成近黑，主按钮是黑底白字；无色 Tag 还会落到白字浅底。
 * 主色改成应用重点蓝，和暗色、侧栏、链接同一套。
 */
const LIGHT_PRIMARY_TOKENS = {
  colorPrimary: '#339cff',
  colorPrimaryHover: '#1f8df0',
  colorPrimaryActive: '#1678d2',
  colorPrimaryBg: '#e8f3ff',
  colorPrimaryBgHover: '#d6ecff',
  colorPrimaryBorder: '#91caff',
  colorPrimaryBorderHover: '#69b1ff',
  colorPrimaryText: '#1268b8',
  colorPrimaryTextHover: '#1f8df0',
  colorPrimaryTextActive: '#1678d2',
  colorTextOnPrimary: '#ffffff',
};

const carbonThemeBase = generateThemeByToken({
  ...carbonThemeJson,
  ...LIGHT_PRIMARY_TOKENS,
});

export const carbonTheme = {
  ...carbonThemeBase,
  theme: {
    ...carbonThemeBase.theme,
    components: {
      ...carbonThemeBase.theme?.components,
      Button: {
        ...carbonThemeBase.theme?.components?.Button,
        primaryColor: '#ffffff',
        defaultBg: '#ffffff',
        defaultColor: '#5f6368',
        defaultBorderColor: '#c8cdd2',
        defaultHoverBg: '#f0f2f4',
        defaultHoverColor: '#1a1c1f',
        defaultHoverBorderColor: '#c8cdd2',
        defaultActiveBg: '#e3e6e8',
        defaultActiveColor: '#1a1c1f',
        defaultActiveBorderColor: '#c8cdd2',
      },
    },
  },
};

/**
 * Carbon 暗色把 colorPrimary 做成浅灰白（#E4E4E4），主按钮会像辅助按钮。
 * 这里改成应用重点蓝，和侧栏、链接、可安装标签同一套色。
 */
const DARK_PRIMARY_TOKENS = {
  colorPrimary: '#339cff',
  colorPrimaryHover: '#5ab0ff',
  colorPrimaryActive: '#1f8df0',
  colorPrimaryBg: '#12314d',
  colorPrimaryBgHover: '#1a4a73',
  colorPrimaryBorder: '#2a7ed4',
  colorPrimaryBorderHover: '#5ab0ff',
  colorPrimaryText: '#9fd0ff',
  colorPrimaryTextHover: '#c5e4ff',
  colorPrimaryTextActive: '#83c4ff',
  colorTextOnPrimary: '#ffffff',
};

const carbonDarkThemeBase = generateThemeByToken(
  { ...carbonDarkThemeJson, ...DARK_PRIMARY_TOKENS },
  true,
);

export const carbonDarkTheme = {
  ...carbonDarkThemeBase,
  theme: {
    ...carbonDarkThemeBase.theme,
    components: {
      ...carbonDarkThemeBase.theme?.components,
      Button: {
        ...carbonDarkThemeBase.theme?.components?.Button,
        primaryColor: '#ffffff',
        defaultBg: '#222222',
        defaultColor: '#d6d6d6',
        defaultBorderColor: '#4a4a4a',
        defaultHoverBg: '#333333',
        defaultHoverColor: '#ffffff',
        defaultHoverBorderColor: '#4a4a4a',
        defaultActiveBg: '#3d3d3d',
        defaultActiveColor: '#ffffff',
        defaultActiveBorderColor: '#4a4a4a',
      },
    },
  },
};
