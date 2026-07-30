/** AgentScope carbon token 到 Ant Design ThemeConfig 的转换。 */

import carbonDarkThemeJson from '@agentscope-ai/design/lib/antd/themes/carbonDarkTheme.json';
import carbonThemeJson from '@agentscope-ai/design/lib/antd/themes/carbonTheme.json';
import generateThemeByToken from '@agentscope-ai/design/lib/antd/themes/generateThemeByToken';

export const carbonTheme = generateThemeByToken(carbonThemeJson);
export const carbonDarkTheme = generateThemeByToken(carbonDarkThemeJson, true);
