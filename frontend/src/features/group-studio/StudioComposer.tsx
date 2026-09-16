/** 群聊输入器。@ 弹出成员菜单，选中写入 mentions[]；文件和 Chat 一样在输入框附上再发送。 */

import { useMemo, useState, type KeyboardEventHandler, type ReactNode } from 'react';
import { AtSign, FileText, Paperclip, Send, X } from 'lucide-react';
import { FileImportButton } from '../../components/FileImportButton';
import { Tooltip } from '@/components/design';
import type { GroupStudioMember } from './types';

export interface StudioComposerAttachment {
  id: string;
  file: File;
  name: string;
  size: number;
}

interface StudioComposerProps {
  disabled: boolean;
  sending: boolean;
  value: string;
  placeholder: string;
  members: GroupStudioMember[];
  mentions: string[];
  fileAttachments: StudioComposerAttachment[];
  onChange: (value: string) => void;
  onMentionsChange: (memberIds: string[]) => void;
  onFile: (file: File) => void;
  onRemoveFile: (id: string) => void;
  onKeyDown: KeyboardEventHandler<HTMLTextAreaElement>;
  onSend: () => void;
  controls?: ReactNode;
}

export function StudioComposer({
  disabled,
  sending,
  value,
  placeholder,
  members,
  mentions,
  fileAttachments,
  onChange,
  onMentionsChange,
  onFile,
  onRemoveFile,
  onKeyDown,
  onSend,
  controls,
}: StudioComposerProps) {
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');

  const mentionable = useMemo(
    () => members.filter((member) => member.status !== 'removed' && member.role !== 'host'),
    [members],
  );

  const mentionedMembers = useMemo(
    () => mentionable.filter((member) => mentions.includes(member.id)),
    [mentionable, mentions],
  );

  const candidates = useMemo(() => {
    const q = mentionQuery.trim().toLowerCase();
    return mentionable.filter((member) => {
      if (!q) {
        return true;
      }
      return member.displayName.toLowerCase().includes(q);
    });
  }, [mentionable, mentionQuery]);

  const syncMentionFromValue = (next: string) => {
    const at = Math.max(next.lastIndexOf('@'), next.lastIndexOf('＠'));
    if (at >= 0 && (at === 0 || /\s/.test(next[at - 1] || ''))) {
      const query = next.slice(at + 1);
      if (!query.includes(' ') && !query.includes('\n')) {
        setMentionOpen(true);
        setMentionQuery(query.replace(/^＠/, ''));
        return;
      }
    }
    setMentionOpen(false);
    setMentionQuery('');
  };

  const pickMember = (member: GroupStudioMember) => {
    const at = Math.max(value.lastIndexOf('@'), value.lastIndexOf('＠'));
    const prefix = at >= 0 ? value.slice(0, at) : value;
    onChange(`${prefix}@${member.displayName} `);
    if (!mentions.includes(member.id)) {
      onMentionsChange([...mentions, member.id]);
    }
    setMentionOpen(false);
    setMentionQuery('');
  };

  const openMentionMenu = () => {
    if (!value.endsWith('@') && !value.endsWith('＠')) {
      const needsSpace = value.length > 0 && !/\s$/.test(value);
      const next = `${value}${needsSpace ? ' ' : ''}@`;
      onChange(next);
      syncMentionFromValue(next);
      return;
    }
    setMentionOpen(true);
    setMentionQuery('');
  };

  return (
    <div className="px-8 pb-6 pt-2">
      <div className="chat-composer relative mx-auto max-w-4xl rounded-xl bg-[var(--color-bg-secondary)]">
        {mentionOpen ? (
          <ul
            className="max-h-44 overflow-auto border-b border-[var(--color-border)] bg-[var(--color-bg-primary)] py-1"
            role="listbox"
            aria-label="选择要发言的成员"
          >
            {candidates.length === 0 ? (
              <li className="px-3 py-2 text-xs text-[var(--color-text-tertiary)]">
                {mentionable.length === 0 ? '群里还没有人，先在右侧拉人进来' : '没有匹配的成员'}
              </li>
            ) : (
              candidates.map((member) => (
                <li key={member.id}>
                  <button
                    type="button"
                    className="flex w-full items-center justify-between px-3 py-1.5 text-left text-sm hover:bg-[var(--color-bg-secondary)]"
                    onMouseDown={(event) => {
                      event.preventDefault();
                      pickMember(member);
                    }}
                  >
                    <span>{member.displayName}</span>
                    {member.muted ? <span className="text-xs text-[var(--color-text-tertiary)]">已静音</span> : null}
                  </button>
                </li>
              ))
            )}
          </ul>
        ) : null}
        <textarea
          value={value}
          disabled={disabled}
          onChange={(event) => {
            const next = event.target.value;
            onChange(next);
            syncMentionFromValue(next);
          }}
          onKeyDown={(event) => {
            if (mentionOpen && event.key === 'Escape') {
              event.preventDefault();
              setMentionOpen(false);
              return;
            }
            if (mentionOpen && (event.key === 'Enter' || event.key === 'Tab') && candidates[0]) {
              event.preventDefault();
              pickMember(candidates[0]);
              return;
            }
            onKeyDown(event);
          }}
          placeholder={placeholder}
          rows={2}
          className="block max-h-[180px] w-full resize-none rounded-t-lg border-0 bg-transparent px-4 pb-0 pt-3 text-base text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:bg-[var(--color-bg-tertiary)]"
          aria-label="群聊消息输入框"
        />
        {mentionedMembers.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 px-3 pt-1" aria-label="已点名成员">
            {mentionedMembers.map((member) => (
              <span
                key={member.id}
                className="inline-flex max-w-48 items-center gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-2 py-0.5 text-xs text-[var(--color-text-secondary)]"
              >
                <span className="truncate">@{member.displayName}</span>
                <button
                  type="button"
                  className="icon-button flex h-4 w-4 shrink-0 items-center justify-center rounded text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                  onClick={() => onMentionsChange(mentions.filter((id) => id !== member.id))}
                  aria-label={`取消点名 ${member.displayName}`}
                >
                  <X size={10} />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        {fileAttachments.length > 0 ? (
          <div className="flex max-h-[104px] flex-wrap gap-2 overflow-y-auto px-3 py-2">
            {fileAttachments.map((file) => (
              <div
                key={file.id}
                className="flex max-w-64 items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-2 py-1.5 text-xs text-[var(--color-text-secondary)]"
              >
                <FileText size={14} className="shrink-0" />
                <span className="truncate">{file.name}</span>
                <button
                  type="button"
                  onClick={() => onRemoveFile(file.id)}
                  className="icon-button ml-auto flex h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                  aria-label={`移除附件 ${file.name}`}
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <div className="flex items-center justify-between gap-2 px-3 py-2">
          <div className="flex min-w-0 items-center gap-2">
            <Tooltip title="点名成员" placement="top" mode="dark">
              <button
                type="button"
                className="icon-button flex h-8 w-8 items-center justify-center rounded-[10px] border border-[var(--color-border)] bg-[var(--color-bg-primary)] text-[var(--color-text-secondary)] transition-colors duration-200 hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
                disabled={disabled || sending}
                onClick={openMentionMenu}
                aria-label="点名成员"
              >
                <AtSign size={16} />
              </button>
            </Tooltip>
            <Tooltip title="上传文件" placement="top" mode="dark">
              <span className="inline-flex">
                <FileImportButton
                  onFile={onFile}
                  disabled={disabled || sending || fileAttachments.length >= 10}
                  className="icon-button h-8 w-8 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-0 text-[var(--color-text-secondary)] transition-colors duration-200 hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                >
                  <Paperclip size={16} />
                </FileImportButton>
              </span>
            </Tooltip>
            {controls}
          </div>
          <button
            type="button"
            className="icon-button flex h-8 w-8 items-center justify-center rounded-[10px] border border-[var(--color-border)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] disabled:cursor-not-allowed disabled:opacity-40"
            disabled={disabled || sending || (!value.trim() && fileAttachments.length === 0)}
            onClick={onSend}
            aria-label="发送消息"
          >
            <Send size={15} />
          </button>
        </div>
      </div>
    </div>
  );
}
