/** 资源不再按 Project 绑定。保留空函数，避免旧调用点打到已删除的接口。 */

export async function bindProjectResource(
  _projectSlug: string,
  _resourceType: string,
  _resourceId: string,
): Promise<void> {
  return undefined;
}
