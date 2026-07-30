/** Chat 图片预览、普通文件校验和 Session 文件上传。 */

import { useCallback, useRef, useState } from 'react';
import { createId } from '../../utils/id';
import type { ChatFileAttachment, ChatFilePayload, ChatImageAttachment } from './types';

interface UseChatAttachmentsOptions {
  onError: (message: string | null) => void;
}

export function useChatAttachments({ onError }: UseChatAttachmentsOptions) {
  const [imageAttachments, setImageAttachments] = useState<ChatImageAttachment[]>([]);
  const [fileAttachments, setFileAttachments] = useState<ChatFileAttachment[]>([]);
  const imageInputRef = useRef<HTMLInputElement>(null);

  const handleImageFiles = useCallback(async (files: FileList | null) => {
    if (!files?.length) return;
    const imageFiles = Array.from(files).filter((file) => file.type.startsWith('image/'));
    const nextAttachments = await Promise.all(imageFiles.map((file) => (
      new Promise<ChatImageAttachment>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const previewUrl = String(reader.result);
          const [, data = ''] = previewUrl.split(',', 2);
          resolve({
            id: createId(),
            name: file.name,
            media_type: file.type || 'image/png',
            data,
            previewUrl,
          });
        };
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      })
    )));
    setImageAttachments((current) => [...current, ...nextAttachments].slice(0, 4));
    onError(null);
    if (imageInputRef.current) imageInputRef.current.value = '';
  }, [onError]);

  const removeImageAttachment = useCallback((id: string) => {
    setImageAttachments((current) => current.filter((item) => item.id !== id));
  }, []);

  const handleFile = useCallback((file: File) => {
    if (file.size > 50 * 1024 * 1024) {
      onError('单个附件不能超过 50 MB。');
      return;
    }
    setFileAttachments((current) => {
      if (current.length >= 10) {
        onError('单次最多上传 10 个附件。');
        return current;
      }
      if (current.some((item) => item.name === file.name && item.size === file.size)) return current;
      return [...current, { id: createId(), file, name: file.name, size: file.size }];
    });
    onError(null);
  }, [onError]);

  const removeFileAttachment = useCallback((id: string) => {
    setFileAttachments((current) => current.filter((item) => item.id !== id));
  }, []);

  const resetAttachments = useCallback(() => {
    setImageAttachments([]);
    setFileAttachments([]);
    if (imageInputRef.current) imageInputRef.current.value = '';
  }, []);

  const uploadFilesForSession = useCallback(async (
    files: ChatFileAttachment[],
    projectId: string,
    sessionId: string,
  ): Promise<ChatFilePayload[]> => Promise.all(files.map(async (attachment) => {
    const form = new FormData();
    form.append('file', attachment.file, attachment.name);
    const params = new URLSearchParams({ sessionId });
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/files?${params.toString()}`, {
      method: 'POST',
      body: form,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(String(payload.detail || `附件 ${attachment.name} 上传失败`));
    return {
      name: String(payload.name || attachment.name),
      path: String(payload.path || ''),
      size: Number(payload.size || attachment.size),
      media_type: String(payload.mediaType || attachment.file.type || 'application/octet-stream'),
    };
  })), []);

  return {
    fileAttachments,
    handleFile,
    handleImageFiles,
    imageAttachments,
    imageInputRef,
    removeFileAttachment,
    removeImageAttachment,
    resetAttachments,
    uploadFilesForSession,
  };
}
