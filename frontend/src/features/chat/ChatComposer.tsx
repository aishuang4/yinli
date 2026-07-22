/** Chat 输入器，负责附件预览、运行模式和发送控制。 */

import type { ChangeEvent, KeyboardEventHandler, ReactNode, RefObject } from 'react';
import { AlertCircle, FileText, ImagePlus, Paperclip, Send, Square, X } from 'lucide-react';
import {
  SparkDeepThinkLine,
  SparkReinforcementLearningLine,
  SparkRenyizhuanrenyiAnyToanyLine,
} from '@agentscope-ai/icons';
import { Select, Tooltip } from '@agentscope-ai/design';
import { FileImportButton } from '../../components/FileImportButton';
import type { ChatFileAttachment, ChatImageAttachment } from './types';

interface ChatComposerProps {
  agentOptions: Array<{ label: string; value: string }>;
  agentsLoading: boolean;
  chatError: string | null;
  deepThinking: boolean;
  fileAttachments: ChatFileAttachment[];
  imageAttachments: ChatImageAttachment[];
  imageInputRef: RefObject<HTMLInputElement | null>;
  inputValue: string;
  isStreaming: boolean;
  messageInputRef: RefObject<HTMLTextAreaElement | null>;
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
}

export function ChatComposer(props: ChatComposerProps) {
  const {
    agentOptions, agentsLoading, chatError, deepThinking, fileAttachments,
    imageAttachments, imageInputRef, inputValue, isStreaming, messageInputRef,
    projectResourcesLoading, selectedAgentId, targetMode, onAbort,
    onCompositionEnd, onCompositionStart, onFile, onImageFiles, onInputChange,
    onKeyDown, onRemoveFile, onRemoveImage, onSelectedAgentChange, onSend,
    onTargetModeChange, onToggleDeepThinking,
  } = props;

  return (
    <div className="px-8 pb-6 pt-2">
      {chatError && (
        <div className="mb-2 flex items-center gap-2 rounded-md border border-[var(--color-error)] bg-[var(--color-error-bg)] px-3 py-2 text-sm text-[var(--color-error)]">
          <AlertCircle size={14} />
          {chatError}
        </div>
      )}
      <div className="chat-composer mx-auto max-w-4xl rounded-xl bg-[var(--color-bg-secondary)]">
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
          onChange={(event) => onInputChange(event.target.value)}
          onKeyDown={onKeyDown}
          onCompositionStart={onCompositionStart}
          onCompositionEnd={onCompositionEnd}
          placeholder={isStreaming ? 'Agent 正在回复中...' : '输入消息...'}
          rows={2}
          disabled={isStreaming}
          className="block max-h-[180px] w-full resize-none rounded-t-lg border-0 bg-transparent px-4 pb-0 pt-3 text-base text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:bg-[var(--color-bg-tertiary)]"
          aria-label="消息输入框"
        />

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

        <div className="flex flex-wrap items-center gap-2 px-3 py-2">
          <Tooltip title="上传图片" placement="top" mode="dark">
            <span className="inline-flex">
              <button
                type="button"
                className="icon-button h-8 w-8 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-bg-primary)] text-[var(--color-text-secondary)] transition-colors duration-200 hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
                onClick={() => imageInputRef.current?.click()}
                disabled={isStreaming || imageAttachments.length >= 4}
                aria-label="上传图片"
              >
                <ImagePlus size={16} />
              </button>
            </span>
          </Tooltip>
          <Tooltip title="上传文件" placement="top" mode="dark">
            <span className="inline-flex">
              <FileImportButton
                onFile={onFile}
                disabled={isStreaming || fileAttachments.length >= 10}
                className="icon-button h-8 w-8 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-0 text-[var(--color-text-secondary)] transition-colors duration-200 hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
              >
                <Paperclip size={16} />
              </FileImportButton>
            </span>
          </Tooltip>
          <div className="flex h-8 gap-1 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-[3px]">
            <ModeButton
              active={targetMode === 'agent'}
              disabled={isStreaming}
              label="单 Agent"
              onClick={() => onTargetModeChange('agent')}
              icon={<SparkReinforcementLearningLine size={16} />}
            />
            <ModeButton
              active={targetMode === 'team'}
              disabled={isStreaming}
              label="Team"
              onClick={() => onTargetModeChange('team')}
              icon={<SparkRenyizhuanrenyiAnyToanyLine size={16} />}
            />
          </div>
          {targetMode === 'agent' && (
            <Select
              value={selectedAgentId}
              onChange={onSelectedAgentChange}
              options={agentOptions}
              loading={agentsLoading || projectResourcesLoading}
              disabled={isStreaming}
              placeholder="选择 Agent"
              style={{ width: 160 }}
            />
          )}
          <div className="ml-auto flex items-center gap-2">
            <Tooltip title={deepThinking ? '深度思考已开启' : '开启深度思考'} placement="top" mode="dark">
              <span className="inline-flex">
                <button
                  type="button"
                  className={`icon-button h-8 w-8 rounded-[10px] border transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-40 ${
                    deepThinking
                      ? 'border-[var(--color-text-primary)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] shadow-sm'
                      : 'border-[var(--color-border)] bg-[var(--color-bg-primary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
                  }`}
                  onClick={onToggleDeepThinking}
                  disabled={isStreaming}
                  aria-label={deepThinking ? '关闭深度思考' : '开启深度思考'}
                  aria-pressed={deepThinking}
                >
                  <SparkDeepThinkLine size={16} />
                </button>
              </span>
            </Tooltip>
            <Tooltip title={isStreaming ? '停止回复' : '发送消息'} placement="top" mode="dark">
              <span className="inline-flex">
                <button
                  type="button"
                  className="icon-button h-8 w-8 rounded-[10px] border border-[var(--color-text-primary)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] transition-opacity duration-200 hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                  onClick={isStreaming ? onAbort : onSend}
                  disabled={!isStreaming && !inputValue.trim() && imageAttachments.length === 0 && fileAttachments.length === 0}
                  aria-label={isStreaming ? '停止回复' : '发送消息'}
                >
                  {isStreaming ? <Square size={16} /> : <Send size={16} />}
                </button>
              </span>
            </Tooltip>
          </div>
        </div>
      </div>
    </div>
  );
}

function ModeButton({
  active,
  disabled,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  disabled: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <Tooltip title={label} placement="top" mode="dark">
      <span className="inline-flex">
        <button
          type="button"
          className={`icon-button h-6 w-6 rounded-[10px] border transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-40 ${
            active
              ? 'border-[var(--color-text-primary)] bg-[var(--color-text-primary)] text-[var(--color-bg-primary)] shadow-sm'
              : 'border-transparent bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
          }`}
          disabled={disabled}
          onClick={onClick}
          aria-label={label}
          aria-pressed={active}
        >
          {icon}
        </button>
      </span>
    </Tooltip>
  );
}
