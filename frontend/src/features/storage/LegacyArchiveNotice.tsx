/** 只展示后端封存完成后的一次性提示。请求失败或 pending 为 false 时不弹出。 */

import { useEffect, useState } from 'react';
import { Button, Modal } from '@/components/design';

interface LegacyNotice {
  pending: boolean;
  path?: string;
  title?: string;
  message?: string;
}

export function LegacyArchiveNotice() {
  const [notice, setNotice] = useState<LegacyNotice | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch('/api/db/legacy-notice', { cache: 'no-store' });
        if (!response.ok) return;
        const body = await response.json() as LegacyNotice;
        if (!cancelled && body.pending && body.path && body.title && body.message) {
          setNotice(body);
        }
      } catch {
        // 后端没给出封存结果时不猜测。
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const acknowledge = async () => {
    setSubmitting(true);
    setError('');
    try {
      const response = await fetch('/api/db/legacy-notice/ack', { method: 'POST' });
      if (!response.ok) {
        setError('确认失败，请稍后再试。');
        return;
      }
      setNotice(null);
    } catch {
      setError('确认失败，请稍后再试。');
    } finally {
      setSubmitting(false);
    }
  };

  if (!notice?.pending) return null;

  return (
    <Modal
      open
      title={notice.title}
      width={480}
      centered
      closable={false}
      maskClosable={false}
      onCancel={() => void acknowledge()}
      footer={(
        <div className="flex justify-end">
          <Button type="primary" loading={submitting} onClick={() => void acknowledge()}>
            知道了
          </Button>
        </div>
      )}
    >
      <p className="text-sm leading-6 text-[var(--color-text-secondary)]">{notice.message}</p>
      <p className="mt-3 break-all font-mono text-xs leading-5 text-[var(--color-text-primary)]">{notice.path}</p>
      {error ? <p className="mt-3 text-sm text-[#b42318]">{error}</p> : null}
    </Modal>
  );
}
