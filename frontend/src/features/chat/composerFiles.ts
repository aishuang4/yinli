/** 把拖入或粘贴的文件交给现有图片 / 附件入口。 */

const IMAGE_NAME = /\.(png|jpe?g|gif|webp|bmp|svg|heic|heif)$/i;

export interface FileCarrier {
  files?: ArrayLike<File> | null;
  items?: ArrayLike<{ kind: string; getAsFile?: () => File | null }> | null;
  types?: ArrayLike<string> | null;
}

export function isImageFile(file: File): boolean {
  return file.type.startsWith('image/') || IMAGE_NAME.test(file.name);
}

export function isFileDrag(data: FileCarrier | null | undefined): boolean {
  if (!data?.types) return false;
  return Array.from(data.types).includes('Files');
}

/** 优先读 files；截图粘贴有时只放在 items 里。 */
export function filesFromTransfer(data: FileCarrier | null | undefined): File[] {
  if (!data) return [];
  const listed = data.files ? Array.from(data.files) : [];
  if (listed.length > 0) return listed;
  if (!data.items) return [];
  const files: File[] = [];
  for (const item of Array.from(data.items)) {
    if (item.kind !== 'file') continue;
    const file = item.getAsFile?.();
    if (file) files.push(file);
  }
  return files;
}

export function partitionComposerFiles(files: File[]): { images: File[]; others: File[] } {
  const images: File[] = [];
  const others: File[] = [];
  let unnamed = 0;
  files.forEach((file) => {
    const named = file.name
      ? file
      : new File([file], `paste-${++unnamed}.${extensionOf(file)}`, { type: file.type });
    if (isImageFile(named)) images.push(named);
    else others.push(named);
  });
  return { images, others };
}

export async function ingestComposerFiles(
  files: File[],
  handlers: {
    onImageFiles: (files: File[]) => void | Promise<void>;
    onFile: (file: File) => void | Promise<void>;
  },
): Promise<void> {
  const { images, others } = partitionComposerFiles(files);
  if (images.length > 0) await handlers.onImageFiles(images);
  for (const file of others) {
    await handlers.onFile(file);
  }
}

function extensionOf(file: File): string {
  const subtype = file.type.split('/')[1]?.split('+')[0];
  if (subtype === 'jpeg') return 'jpg';
  return subtype || 'bin';
}
