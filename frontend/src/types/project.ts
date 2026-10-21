/** Project 是唯一的资源上下文。身份是 slug。 */

export interface ProjectFolder {
  path: string;
  accessMode: 'read' | 'readwrite';
  isPrimary: boolean;
  grantedAt: string | null;
}

export interface ProjectRecord {
  slug: string;
  name: string;
  primaryPath: string;
  color: string | null;
  icon: string | null;
  status: string;
  createdAt: string;
  updatedAt: string;
  archivedAt: string | null;
  folders: ProjectFolder[];
}
