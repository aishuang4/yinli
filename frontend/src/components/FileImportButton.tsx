/**
 * FileImportButton — 基于 Spark Design Upload 的本地文件导入按钮。
 *
 * 只负责选择文件并交给业务回调，不触发自动上传请求。
 */

import type { ComponentProps, ReactNode } from 'react';
import { Button, Upload as DesignUpload } from '@/components/design';

type FileImportButtonProps = {
  accept?: string;
  children: ReactNode;
  icon?: ReactNode;
  disabled?: boolean;
  loading?: boolean;
  size?: ComponentProps<typeof Button>['size'];
  type?: ComponentProps<typeof Button>['type'];
  className?: string;
  onFile: (file: File) => void | Promise<void>;
};

export function FileImportButton({
  accept,
  children,
  icon,
  disabled = false,
  loading = false,
  size,
  type,
  className,
  onFile,
}: FileImportButtonProps) {
  return (
    <DesignUpload
      accept={accept}
      className="file-import-button-upload"
      disabled={disabled || loading}
      maxCount={1}
      showUploadList={false}
      beforeUpload={(file) => {
        void onFile(file as File);
        return false;
      }}
    >
      <Button
        className={className}
        disabled={disabled}
        loading={loading}
        size={size}
        type={type}
      >
        {icon}
        {children}
      </Button>
    </DesignUpload>
  );
}
