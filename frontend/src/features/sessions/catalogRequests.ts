/** 全局 catalog 路徑。管理頁不附帶 Session、Workspace 或 Project query。 */

export const GLOBAL_CATALOG_URLS = [
  '/api/agents',
  '/api/skills',
  '/api/skills/categories',
  '/api/skills/enabled',
  '/api/mcp',
  '/api/mcp/tools',
  '/api/tools/http',
  '/api/settings/models',
] as const;

export function requestHasHiddenScope(url: string): boolean {
  const query = url.split('?')[1] ?? '';
  const keys = ['workspace', 'project', 'session'].map((name) => `${name}${'Id'}=`);
  return keys.some((key) => query.split('&').some((part) => part.startsWith(key)));
}
