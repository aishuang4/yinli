/** Chat 输入器，负责附件预览、运行模式和发送控制。 */

import { useEffect, useMemo, useRef, useState, type ChangeEvent, type KeyboardEventHandler, type ReactNode, type RefObject } from 'react';
import { AlertCircle, FileText, Send, Square, X } from 'lucide-react';
import { Tooltip } from '@/components/design';
import { FileImportButton } from '../../components/FileImportButton';
import { ChatAgentChip, useChatComposerAgents } from './ChatAgentSelect';
import { ChatComposerPlusMenu, ChatDeepThinkChip, ChatPlanChip } from './ChatComposerPlusMenu';
import type { PlusMenuCategory } from './plusMenu';
import { ChatForcedSkillChips, ChatSkillSlashMenu } from './ChatSkillPicker';
import {
  consumeSlashQuery,
  filterSkillsBySlashQuery,
  readSlashQuery,
  toggleForcedSkillId,
} from './forcedSkills';
import type { Skill } from '../../types/skill';
import type { ChatFileAttachment, ChatImageAttachment } from './types';

interface ChatComposerProps {
  modelSelector?: ReactNode;
  agentsLoading: boolean;
  chatError: string | null;
  deepThinking: boolean;
  fileAttachments: ChatFileAttachment[];
  imageAttachments: ChatImageAttachment[];
  imageInputRef: RefObject<HTMLInputElement | null>;
  inputValue: string;
  isStreaming: boolean;
  messageInputRef: RefObject<HTMLTextAreaElement | null>;
  planMode: boolean;
  projectResourcesLoading: boolean;
  selectedAgentId: string;
  targetMode: 'agent' | 'team';
  onAbort: () => void;
  onCompositionEnd: () => void;
  onCompositionStart: () => void;
  onFile: (file: File) => void | Promise<void>;
  onImageFiles: (files: FileList | null) => void | Promise<void>;
  onInputChange: (value: string) => void;
  onKeyDown: KeyboardEventHandler<HTMLTextAreaElement>;
  onRemoveFile: (id: string) => void;
  onRemoveImage: (id: string) => void;
  onSelectedAgentChange: (id: string) => void;
  onSend: () => void;
  onTargetModeChange: (mode: 'agent' | 'team') => void;
  onToggleDeepThinking: () => void;
  onTogglePlanMode: () => void;
  teamModeEnabled?: boolean;
  planModeEnabled?: boolean;
  composerSkills?: Skill[];
  forcedSkillIds?: string[];
  skillsLoading?: boolean;
  onForcedSkillIdsChange?: (ids: string[]) => void;
}

export function ChatComposer(props: ChatComposerProps) {
  const {
    agentsLoading, chatError, deepThinking, fileAttachments,
    imageAttachments, imageInputRef, inputValue, isStreaming, messageInputRef, planMode,
    projectResourcesLoading, selectedAgentId, targetMode, onAbort,
    onCompositionEnd, onCompositionStart, onFile, onImageFiles, onInputChange,
    onKeyDown, onRemoveFile, onRemoveImage, onSelectedAgentChange, onSend,
    onTargetModeChange, onToggleDeepThinking, onTogglePlanMode,
    composerSkills = [], forcedSkillIds = [], skillsLoading = false, onForcedSkillIdsChange,
  } = props;
  const teamModeEnabled = props.teamModeEnabled !== false;
  const planModeEnabled = props.planModeEnabled !== false;
  const [cursor, setCursor] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [plusOpen, setPlusOpen] = useState(false);
  const [plusCategory, setPlusCategory] = useState<PlusMenuCategory>('files');
  const fileImportHostRef = useRef<HTMLSpanElement>(null);
  const { agents, loading: composerAgentsLoading, hostRef: agentHostRef } = useChatComposerAgents();
  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId) ?? agents[0] ?? null;
  const showChips = (targetMode === 'agent' && planModeEnabled && planMode)
    || deepThinking
    || forcedSkillIds.length > 0;
  const slashQuery = onForcedSkillIdsChange ? readSlashQuery(inputValue, cursor) : null;
  const slashOpen = Boolean(slashQuery) && !slashDismissed && !isStreaming;
  const slashSkills = useMemo(
    () => filterSkillsBySlashQuery(composerSkills, slashQuery?.query || ''),
    [composerSkills, slashQuery?.query],
  );

  useEffect(() => {
    setSlashDismissed(false);
    setHighlightedIndex(0);
  }, [slashQuery?.start, slashQuery?.query]);

  useEffect(() => {
    if (highlightedIndex >= slashSkills.length) {
      setHighlightedIndex(0);
    }
  }, [highlightedIndex, slashSkills.length]);

  const applyCursor = (next: string, nextCursor: number) => {
    onInputChange(next);
    setCursor(nextCursor);
    requestAnimationFrame(() => {
      const el = messageInputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(nextCursor, nextCursor);
    });
  };

  const pinForcedSkill = (skill: Skill) => {
    if (!onForcedSkillIdsChange) return;
    if (!forcedSkillIds.includes(skill.id)) {
      onForcedSkillIdsChange(toggleForcedSkillId(forcedSkillIds, skill.id));
    }
  };

  const pickSlashSkill = (skill: Skill) => {
    if (!slashQuery) return;
    pinForcedSkill(skill);
    const next = consumeSlashQuery(inputValue, slashQuery);
    applyCursor(next, Math.min(slashQuery.start, next.length));
    setSlashDismissed(true);
  };

  const triggerFileImport = () => {
    fileImportHostRef.current?.querySelector('button')?.click();
  };

  const handleComposerKeyDown: KeyboardEventHandler<HTMLTextAreaElement> = (event) => {
    if (slashOpen) {
      if (event.key === 'Escape') {
        event.preventDefault();
        setSlashDismissed(true);
        return;
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setHighlightedIndex((index) => (slashSkills.length === 0 ? 0 : (index + 1) % slashSkills.length));
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setHighlightedIndex((index) => (
          slashSkills.length === 0 ? 0 : (index - 1 + slashSkills.length) % slashSkills.length
        ));
        return;
      }
      if ((event.key === 'Enter' || event.key === 'Tab') && !event.nativeEvent.isComposing && slashSkills[highlightedIndex]) {
        event.preventDefault();
        pickSlashSkill(slashSkills[highlightedIndex]);
        return;
      }
    }
    onKeyDown(event);
  };

  return (
    <div className="px-8 pb-6 pt-2">
      {chatError && (
        <div className="mb-2 flex items-center gap-2 rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] px-3 py-2 text-sm text-[var(--color-error)]">
          <AlertCircle size={14} />
          {chatError}
        </div>
      )}
      <div className="chat-composer relative mx-auto max-w-4xl rounded-xl bg-[var(--color-bg-secondary)]">
        {slashOpen ? (
          <ChatSkillSlashMenu
            skills={slashSkills}
            selectedIds={forcedSkillIds}
            highlightedIndex={highlightedIndex}
            loading={skillsLoading}
            query={slashQuery?.query || ''}
            onHighlight={setHighlightedIndex}
            onPick={pickSlashSkill}
          />
        ) : null}
        <input
          ref={imageInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(event: ChangeEvent<HTMLInputElement>) => onImageFiles(event.target.files)}
        />
        <textarea
          ref={messageInputRef}
          value={inputValue}
          onChange={(event) => {
            onInputChange(event.target.value);
            setCursor(event.target.selectionStart ?? event.target.value.length);
          }}
          onSelect={(event) => setCursor(event.currentTarget.selectionStart ?? 0)}
          onClick={(event) => setCursor(event.currentTarget.selectionStart ?? 0)}
          onKeyUp={(event) => setCursor(event.currentTarget.selectionStart ?? 0)}
          onKeyDown={handleComposerKeyDown}
          onCompositionStart={onCompositionStart}
          onCompositionEnd={onCompositionEnd}
          placeholder={isStreaming ? '给正在执行的 Agent 补充信息...' : '输入消息，或用 / 选择 Skill...'}
          rows={2}
          className="block max-h-[180px] w-full resize-none rounded-t-lg border-0 bg-transparent px-4 pb-0 pt-3 text-base text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)]"
          aria-label="消息输入框"
        />
        {showChips ? (
          <div className="flex flex-wrap gap-1.5 px-3 pt-2">
            {targetMode === 'agent' && planModeEnabled && planMode ? (
              <ChatPlanChip disabled={isStreaming} onRemove={onTogglePlanMode} />
            ) : null}
            {deepThinking ? (
              <ChatDeepThinkChip disabled={isStreaming} onRemove={onToggleDeepThinking} />
            ) : null}
            {onForcedSkillIdsChange ? (
              <ChatForcedSkillChips
                skills={composerSkills}
                selectedIds={forcedSkillIds}
                disabled={isStreaming}
                onRemove={(skillId) => onForcedSkillIdsChange(forcedSkillIds.filter((id) => id !== skillId))}
              />
            ) : null}
          </div>
        ) : null}

        {imageAttachments.length > 0 && (
          <div className="flex max-h-[88px] flex-wrap gap-2 overflow-hidden px-3 py-2">
            {imageAttachments.map((image) => (
              <div key={image.id} className="group relative h-16 w-16 overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)]">
                <img src={image.previewUrl} alt={image.name} className="h-full w-full object-cover" />
                <button
                  type="button"
                  onClick={() => onRemoveImage(image.id)}
                  className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded bg-black/60 text-white opacity-0 transition-opacity group-hover:opacity-100"
                  aria-label="移除图片"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}

        {fileAttachments.length > 0 && (
          <div className="flex max-h-[104px] flex-wrap gap-2 overflow-y-auto px-3 py-2">
            {fileAttachments.map((file) => (
              <div key={file.id} className="flex max-w-64 items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-2 py-1.5 text-xs text-[var(--color-text-secondary)]">
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
        )}

        <span ref={fileImportHostRef} aria-hidden="true" className="pointer-events-none absolute h-px w-px overflow-hidden opacity-0">
          <FileImportButton
            onFile={onFile}
            disabled={isStreaming || fileAttachments.length >= 10}
            className="h-8 w-8 p-0"
          >
            上传文件
          </FileImportButton>
        </span>
        <div className="flex flex-wrap items-center gap-2 px-3 py-2">
          <ChatComposerPlusMenu
            open={plusOpen}
            category={plusCategory}
            onOpenChange={setPlusOpen}
            onCategoryChange={setPlusCategory}
            retainRef={agentHostRef}
            disabled={isStreaming}
            planMode={planMode}
            deepThinking={deepThinking}
            targetMode={targetMode}
            showPlan={targetMode === 'agent' && planModeEnabled}
            showTeam={teamModeEnabled}
            agents={agents}
            selectedAgentId={selectedAgent?.id || selectedAgentId}
            agentsLoading={composerAgentsLoading || agentsLoading}
            skills={composerSkills}
            selectedSkillIds={forcedSkillIds}
            skillsEnabled={Boolean(onForcedSkillIdsChange)}
            skillsLoading={skillsLoading}
            imageDisabled={imageAttachments.length >= 4}
            fileDisabled={fileAttachments.length >= 10}
            onTogglePlan={onTogglePlanMode}
            onToggleDeepThinking={onToggleDeepThinking}
            onTargetModeChange={onTargetModeChange}
            onPickAgent={onSelectedAgentChange}
            onPickSkill={pinForcedSkill}
            onUploadImage={() => imageInputRef.current?.click()}
            onUploadFile={triggerFileImport}
          />
          <div ref={agentHostRef} className="inline-flex">
            <ChatAgentChip
              agent={selectedAgent}
              teamMode={targetMode === 'team'}
              disabled={isStreaming || agentsLoading || projectResourcesLoading}
              onClick={() => {
                setPlusCategory('agents');
                setPlusOpen(true);
              }}
            />
          </div>
          <div className="chat-composer-model-slot min-w-0 max-w-[168px] shrink">
            {props.modelSelector}
          </div>
          <div className="ml-auto flex items-center gap-2">
            {isStreaming ? (
              <Tooltip title="停止回复" placement="top" mode="dark">
                <span className="inline-flex">
                  <button
                    type="button"
                    className="icon-button h-8 w-8 rounded-[10px] border border-[var(--color-text-primary)] bg-[var(--color-bg-primary)] text-[var(--color-text-primary)] transition-opacity duration-200 hover:opacity-90"
                    onClick={onAbort}
                    aria-label="停止回复"
                  >
                    <Square size={16} />
                  </button>
                </span>
              </Tooltip>
            ) : (
              <Tooltip title="发送消息" placement="top" mode="dark">
                <span className="inline-flex">
                  <button
                    type="button"
                    className="icon-button h-8 w-8 rounded-[10px] border border-[var(--color-text-primary)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] transition-opacity duration-200 hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                    onClick={onSend}
                    disabled={!inputValue.trim() && imageAttachments.length === 0 && fileAttachments.length === 0}
                    aria-label="发送消息"
                  >
                    <Send size={16} />
                  </button>
                </span>
              </Tooltip>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
