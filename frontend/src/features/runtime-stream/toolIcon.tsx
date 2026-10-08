/** 按工具名选择一行里的图标。 */

import {
  Bot,
  FilePenLine,
  FilePlus2,
  FileSearch,
  FileText,
  FolderSearch,
  Globe2,
  ListChecks,
  MessageCircleQuestion,
  Puzzle,
  Terminal,
  Wrench,
} from 'lucide-react';

export function toolIcon(name: string): React.ReactNode {
  const tokens = new Set(
    name
      .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter(Boolean),
  );
  const has = (...names: string[]) => names.some((value) => tokens.has(value));

  if (has('bash', 'shell', 'terminal')) return <Terminal size={14} />;
  if (has('http', 'web', 'fetch')) return <Globe2 size={14} />;
  if (has('read')) return <FileText size={14} />;
  if (has('edit', 'patch')) return <FilePenLine size={14} />;
  if (has('write', 'create')) return <FilePlus2 size={14} />;
  if (has('grep', 'search')) return <FileSearch size={14} />;
  if (has('glob', 'list')) return <FolderSearch size={14} />;
  if (has('ask')) return <MessageCircleQuestion size={14} />;
  if (has('delegate', 'subagent')) return <Bot size={14} />;
  if (has('plan', 'todo', 'task')) return <ListChecks size={14} />;
  if (has('skill')) return <Puzzle size={14} />;
  return <Wrench size={14} />;
}
