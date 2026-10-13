/** 对话主区域拖入文件：回复进行中拒绝，其余交给附件入口。 */

import { useState, type DragEvent } from 'react';
import { filesFromTransfer, ingestComposerFiles, isFileDrag } from './composerFiles';

interface UseChatFileDropOptions {
  enabled: boolean;
  onFile: (file: File) => void | Promise<void>;
  onImageFiles: (files: File[]) => void | Promise<void>;
  onReject: () => void;
}

export function useChatFileDrop({ enabled, onFile, onImageFiles, onReject }: UseChatFileDropOptions) {
  const [dragOver, setDragOver] = useState(false);

  const acceptFiles = (files: File[]) => {
    if (files.length === 0) return;
    if (!enabled) {
      onReject();
      return;
    }
    void ingestComposerFiles(files, { onFile, onImageFiles });
  };

  const staysInside = (event: DragEvent<HTMLElement>) => {
    const next = event.relatedTarget;
    return next instanceof Node && event.currentTarget.contains(next);
  };

  const onDragEnter = (event: DragEvent<HTMLElement>) => {
    if (!isFileDrag(event.dataTransfer)) return;
    event.preventDefault();
    if (staysInside(event)) return;
    setDragOver(true);
  };

  const onDragOver = (event: DragEvent<HTMLElement>) => {
    if (!isFileDrag(event.dataTransfer)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = enabled ? 'copy' : 'none';
  };

  const onDragLeave = (event: DragEvent<HTMLElement>) => {
    if (staysInside(event)) return;
    setDragOver(false);
  };

  const onDrop = (event: DragEvent<HTMLElement>) => {
    const files = filesFromTransfer(event.dataTransfer);
    if (!isFileDrag(event.dataTransfer) && files.length === 0) return;
    event.preventDefault();
    setDragOver(false);
    acceptFiles(files);
  };

  return {
    acceptFiles,
    dragOver,
    dropHandlers: { onDragEnter, onDragLeave, onDragOver, onDrop },
  };
}
